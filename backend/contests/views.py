import hashlib
import json

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Avg, BooleanField, Case, Count, Exists, OuterRef, Prefetch, Value, When
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from .judge_assist import analyze_contest, analyze_submission, ensure_pending_review, run_in_background
from . import tech_stacks
from .llm import available_models
from .matching import recommend_teams_for_user, recommend_users_for_team
from .models import (
    Award,
    Contest,
    Judge,
    Participant,
    Profile,
    Score,
    Submission,
    SubmissionReview,
    Team,
    TechStack,
)
from .profile_extract import extract_profile
from .permissions import (
    IsAssignedJudge,
    IsOrganizer,
    IsOrganizerOrReadOnly,
    IsTeamMemberOrReadOnly,
)
from .serializers import (
    AwardSerializer,
    ContestSerializer,
    JudgeSerializer,
    MeSerializer,
    ParticipantSerializer,
    ProfileSerializer,
    RegisterSerializer,
    ScoreboardEntrySerializer,
    ScoreSerializer,
    SubmissionReviewSerializer,
    SubmissionSerializer,
    TeamSerializer,
    TechStackSerializer,
)

# 대회 상태별 허용 동작. 프론트엔드 `src/rules.ts`와 동일한 규칙을 유지한다.
# 팀 모집은 모집중에서만 열린다 — 대회가 시작된 뒤에 팀이 새로 생기면 발표 순서·심사 배정이
# 이미 정해진 뒤에 인원이 바뀐다. 제출물은 다르다: 진행중이 곧 개발 시간이므로 계속 수정 가능.
TEAM_FORMATION_STATUSES = {Contest.Status.RECRUITING}
SUBMISSION_STATUSES = {Contest.Status.RECRUITING, Contest.Status.ONGOING}
SCORING_STATUSES = {Contest.Status.JUDGING}


def ensure_contest_status(contest, allowed, message):
    """Raise 403 when the contest is not in one of the allowed statuses."""
    if contest.status not in allowed:
        label = contest.get_status_display()
        raise PermissionDenied(f'{message} (현재 상태: {label})')


def is_contest_judge(contest, user):
    """이 사용자가 이 대회의 심사위원인지.

    `ContestViewSet.get_queryset()` 이 이미 `is_judge` 를 EXISTS 로 annotate 해 두므로,
    거기서 온 객체라면 쿼리를 한 번 더 던지지 않는다. 5초마다 폴링되는 스코어보드에서 이
    한 줄이 접속자 수만큼 반복되던 EXISTS 쿼리를 없앤다. annotate 없이 온 객체(다른 뷰,
    직접 조회)도 안전하게 동작하도록 없을 때만 실제 쿼리로 떨어진다.
    """
    annotated = getattr(contest, 'is_judge', None)
    if annotated is not None:
        return bool(annotated)
    return Judge.objects.filter(contest=contest, user=user).exists()


def scoreboard_cache_key(contest_slug, is_privileged):
    # 결선 라운드가 응답에 들어가는지가 요청자 권한에 따라 다르므로 키를 나눈다. 하나로
    # 합치면 캐시가 비공개 순위를 관람객에게 그대로 돌려준다.
    return f'scoreboard:{contest_slug}:{"judge" if is_privileged else "public"}'


def invalidate_scoreboard(contest_slug):
    """점수·팀·제출물이 바뀌면 캐시된 스코어보드를 버린다.

    캐시는 5초 폴링이 접속자 수만큼 겹칠 때 같은 집계를 반복하지 않으려는 것이지, 갱신을
    늦추려는 게 아니다. 쓰기 직후 비워야 다음 폴링이 바로 새 순위를 본다.
    """
    cache.delete_many([
        scoreboard_cache_key(contest_slug, True),
        scoreboard_cache_key(contest_slug, False),
    ])


class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]


class MeView(generics.RetrieveAPIView):
    serializer_class = MeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user


class ContestViewSet(viewsets.ModelViewSet):
    queryset = Contest.objects.all()
    serializer_class = ContestSerializer
    permission_classes = [IsOrganizerOrReadOnly]
    lookup_field = 'slug'

    def get_queryset(self):
        # team_count / is_judge 를 한 쿼리에서 같이 뽑아, 대회 수만큼 COUNT/EXISTS 쿼리가
        # 반복되는 N+1 을 없앤다. 익명 사용자는 is_judge 가 항상 False 다.
        user = self.request.user
        if user.is_authenticated:
            is_judge = Exists(Judge.objects.filter(contest=OuterRef('pk'), user=user))
        else:
            is_judge = Value(False, output_field=BooleanField())
        return Contest.objects.annotate(team_count=Count('teams'), is_judge=is_judge)

    @action(detail=True, methods=['get'], permission_classes=[permissions.AllowAny])
    def scoreboard(self, request, slug=None):
        """Per-round ranking of every team in the contest.

        Entries are grouped by round (preliminary first), ranked by average score
        using competition ranking (ties share a rank, the next rank is skipped).
        Teams without any score in a round come last with ``rank: null``.
        Costs a fixed number of queries regardless of team/score count: one for
        teams (submissions joined) and one grouped aggregate for scores.

        ``final`` round is the composite score (코드/기능 예선 점수 + 발표 점수) and stays
        hidden from the public until the organizer reveals it at the awards ceremony —
        only staff and judges assigned to this contest receive those entries. Everyone
        still sees ``preliminary`` live, same as before.

        Every viewer polls this endpoint every 5 seconds, so the aggregate is cached for
        a few seconds (invalidated on any score/team/submission write) and tagged with an
        ETag — an unchanged board answers ``304`` with no body.
        """
        contest = self.get_object()
        user = request.user
        is_privileged = bool(
            user and user.is_authenticated and (user.is_staff or is_contest_judge(contest, user))
        )

        cache_key = scoreboard_cache_key(contest.slug, is_privileged)
        cached = cache.get(cache_key)
        if cached is None:
            data = self._build_scoreboard(contest, is_privileged)
            # ETag 는 직렬화 결과 자체의 해시다. 순위가 그대로면 같은 값이 나오므로 폴링이
            # 반복돼도 본문을 다시 내려보내지 않는다.
            digest = hashlib.md5(
                json.dumps(data, sort_keys=True, ensure_ascii=False).encode('utf-8')
            ).hexdigest()
            cached = {'data': data, 'etag': f'"{digest}"'}
            cache.set(cache_key, cached, settings.SCOREBOARD_CACHE_SECONDS)

        # no-cache 는 "저장하지 마라"가 아니라 "쓰기 전에 반드시 재검증하라"는 뜻이다.
        # 브라우저가 임의로 오래된 순위를 그대로 보여주는 일을 막는다.
        headers = {'ETag': cached['etag'], 'Cache-Control': 'no-cache'}
        if request.headers.get('If-None-Match') == cached['etag']:
            return Response(status=status.HTTP_304_NOT_MODIFIED, headers=headers)
        return Response(cached['data'], headers=headers)

    def _build_scoreboard(self, contest, is_privileged):
        """Aggregate + rank every team, returning serialized (JSON-safe) entries."""
        teams = list(contest.teams.select_related('submission'))
        aggregates = (
            Score.objects.filter(submission__team__contest=contest)
            .values('submission__team_id', 'round')
            .annotate(average=Avg('value'), count=Count('id'))
        )
        by_key = {(a['submission__team_id'], a['round']): a for a in aggregates}

        visible_rounds = (
            Score.Round.choices if is_privileged
            else [c for c in Score.Round.choices if c[0] != Score.Round.FINAL]
        )
        entries = []
        for round_value, _ in visible_rounds:
            round_entries = []
            for team in teams:
                submission = getattr(team, 'submission', None)
                agg = by_key.get((team.id, round_value), {'average': None, 'count': 0})
                round_entries.append({
                    'team_id': team.id,
                    'team_name': team.name,
                    'submission_title': submission.title if submission else None,
                    'round': round_value,
                    'average_score': agg['average'],
                    'vote_count': agg['count'],
                    'rank': None,
                })

            scored = sorted(
                (e for e in round_entries if e['average_score'] is not None),
                key=lambda e: (-e['average_score'], e['team_name']),
            )
            unscored = sorted(
                (e for e in round_entries if e['average_score'] is None),
                key=lambda e: e['team_name'],
            )
            previous_score, previous_rank = None, 0
            for position, entry in enumerate(scored, start=1):
                if entry['average_score'] != previous_score:
                    previous_rank = position
                    previous_score = entry['average_score']
                entry['rank'] = previous_rank
            entries.extend(scored)
            entries.extend(unscored)

        # 캐시에 넣고 해시할 수 있도록 ReturnList 가 아닌 순수 dict 리스트로 만든다.
        return [dict(entry) for entry in ScoreboardEntrySerializer(entries, many=True).data]

    @action(detail=True, methods=['post'], permission_classes=[IsOrganizerOrReadOnly])
    def assign_presentation_order(self, request, slug=None):
        """Lock in the presentation running order and start time (organizer only).

        Order follows submission time (earliest first); teams that never submitted go
        last, alphabetically. This is only a starting point — the organizer can reorder
        any team afterwards. It sets no start times: presentations begin when the
        organizer presses 발표 시작 on a team.
        """
        contest = self.get_object()
        teams = list(
            contest.teams.select_related('submission').order_by(
                Case(When(submission__isnull=True, then=Value(1)), default=Value(0)),
                'submission__submitted_at',
                'name',
            )
        )
        for index, team in enumerate(teams, start=1):
            team.presentation_order = index
        Team.objects.bulk_update(teams, ['presentation_order'])
        invalidate_scoreboard(contest.slug)
        return Response(ContestSerializer(contest, context=self.get_serializer_context()).data)

    @action(detail=True, methods=['post'], permission_classes=[IsOrganizer])
    def analyze_submissions(self, request, slug=None):
        """이 대회의 제출물 전체를 한 모델로 분석한다 (운영자 전용, 심사 전에 한 번).

        저장소 주소가 없는 제출물은 건너뛴다. 순차로 돌기 때문에 20팀이면 수 분 걸리지만,
        백그라운드 스레드에서 도는 동안에도 서비스는 그대로 응답한다. 진행 상황은 각 제출물의
        `reviews` 로 확인한다 — 여기서는 '분석 중' 행만 만들어 바로 돌려준다.
        """
        contest = self.get_object()
        provider = request.data.get('provider') or None
        model = request.data.get('model') or None
        submissions = list(
            Submission.objects.filter(team__contest=contest).exclude(repo_url='')
        )
        if not submissions:
            return Response(
                {'detail': '분석할 저장소가 등록된 제출물이 없습니다.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        pending = [ensure_pending_review(s, provider, model) for s in submissions]
        if not pending[0].provider:
            return Response(
                {'detail': '설정된 LLM API 키가 없습니다.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        run_in_background(analyze_contest, contest, pending[0].provider, pending[0].model)
        return Response(
            {
                'queued': len(pending),
                'provider': pending[0].provider,
                'model': pending[0].model,
                'reviews': SubmissionReviewSerializer(pending, many=True).data,
            },
            status=status.HTTP_202_ACCEPTED,
        )


class TeamViewSet(viewsets.ModelViewSet):
    serializer_class = TeamSerializer
    # 팀 정보 수정/삭제는 그 팀 참가자(또는 운영자)만 — IsTeamMemberOrReadOnly 의
    # has_object_permission 이 obj 에 .team 이 없으면 obj 자체를 팀으로 보고 검사한다.
    permission_classes = [IsTeamMemberOrReadOnly]

    def get_queryset(self):
        # 참가자의 username 까지 한 번에 가져온다 (팀 목록은 5초마다 폴링되므로 N+1 을 피한다).
        queryset = Team.objects.select_related('contest', 'submission').prefetch_related(
            Prefetch('participants', queryset=Participant.objects.select_related('user'))
        )
        contest_slug = self.request.query_params.get('contest')
        if contest_slug:
            queryset = queryset.filter(contest__slug=contest_slug)
        return queryset

    def perform_create(self, serializer):
        ensure_contest_status(
            serializer.validated_data['contest'], TEAM_FORMATION_STATUSES,
            '모집중 상태에서만 팀을 만들 수 있습니다.',
        )
        team = serializer.save()
        Participant.objects.create(team=team, user=self.request.user)
        invalidate_scoreboard(team.contest_id)

    # 팀 이름·구성이 바뀌면 스코어보드의 행 자체가 달라진다.
    def perform_update(self, serializer):
        team = serializer.save()
        invalidate_scoreboard(team.contest_id)

    def perform_destroy(self, instance):
        contest_id = instance.contest_id
        instance.delete()
        invalidate_scoreboard(contest_id)

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def join(self, request, pk=None):
        team = self.get_object()
        ensure_contest_status(
            team.contest, TEAM_FORMATION_STATUSES,
            '모집중 상태에서만 팀에 참가할 수 있습니다.',
        )
        participant, created = Participant.objects.get_or_create(team=team, user=request.user)
        if not created:
            return Response({'detail': '이미 참가 중인 팀입니다.'}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ParticipantSerializer(participant).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], permission_classes=[IsOrganizer])
    def start_presentation(self, request, pk=None):
        """이 팀의 발표를 지금 시작한다 (운영자만).

        시작 시각을 저장하는 쪽이 시계에 맞춘 예정표보다 현장에 맞는다 — 앞 팀이 늦어져도
        뒤 팀 시간이 깎이지 않고, 팀 교체·쉬는 시간에는 시작된 팀이 없어 타이머가 멈춘다.
        한 대회에서 동시에 두 팀이 발표할 수는 없으므로, 아직 안 끝난 다른 팀은 여기서 끝낸다.
        """
        team = self.get_object()
        now = timezone.now()
        with transaction.atomic():
            Team.objects.filter(
                contest_id=team.contest_id, presentation_started_at__isnull=False,
                presentation_ended_at__isnull=True,
            ).exclude(pk=team.pk).update(presentation_ended_at=now)
            team.presentation_started_at = now
            team.presentation_ended_at = None
            team.save(update_fields=['presentation_started_at', 'presentation_ended_at'])
        return Response(self.get_serializer(team).data)

    @action(detail=True, methods=['post'], permission_classes=[IsOrganizer])
    def end_presentation(self, request, pk=None):
        """발표를 끝낸다 (운영자만). 남은 시간이 있어도 즉시 멈추고, 타이머는 더 흐르지 않는다."""
        team = self.get_object()
        if team.presentation_started_at is None:
            return Response(
                {'detail': '아직 시작하지 않은 발표입니다.'}, status=status.HTTP_400_BAD_REQUEST
            )
        team.presentation_ended_at = timezone.now()
        team.save(update_fields=['presentation_ended_at'])
        return Response(self.get_serializer(team).data)

    @action(detail=True, methods=['post'], permission_classes=[IsOrganizer])
    def reset_presentation(self, request, pk=None):
        """시작/종료 기록을 지운다 (운영자만) — 실수로 눌렀을 때 되돌리는 길."""
        team = self.get_object()
        team.presentation_started_at = None
        team.presentation_ended_at = None
        team.save(update_fields=['presentation_started_at', 'presentation_ended_at'])
        return Response(self.get_serializer(team).data)


class SubmissionViewSet(viewsets.ModelViewSet):
    serializer_class = SubmissionSerializer
    permission_classes = [IsTeamMemberOrReadOnly]

    SUBMISSION_LOCKED_MESSAGE = '심사가 시작된 뒤에는 제출물을 등록하거나 수정할 수 없습니다.'

    def get_queryset(self):
        queryset = Submission.objects.select_related('team', 'team__contest')
        contest_slug = self.request.query_params.get('contest')
        if contest_slug:
            queryset = queryset.filter(team__contest__slug=contest_slug)
        return queryset

    def perform_create(self, serializer):
        team = serializer.validated_data['team']
        if not self.request.user.is_staff and not Participant.objects.filter(
            team=team, user=self.request.user
        ).exists():
            raise PermissionDenied('해당 팀의 참가자만 제출할 수 있습니다.')
        ensure_contest_status(team.contest, SUBMISSION_STATUSES, self.SUBMISSION_LOCKED_MESSAGE)
        serializer.save()
        invalidate_scoreboard(team.contest_id)

    def perform_update(self, serializer):
        contest = serializer.instance.team.contest
        ensure_contest_status(contest, SUBMISSION_STATUSES, self.SUBMISSION_LOCKED_MESSAGE)
        serializer.save()
        # 제출물 제목이 스코어보드에 그대로 실린다.
        invalidate_scoreboard(contest.pk)

    def perform_destroy(self, instance):
        contest_id = instance.team.contest_id
        ensure_contest_status(instance.team.contest, SUBMISSION_STATUSES, self.SUBMISSION_LOCKED_MESSAGE)
        instance.delete()
        invalidate_scoreboard(contest_id)

    @action(detail=True, methods=['post'], permission_classes=[IsOrganizer])
    def analyze(self, request, pk=None):
        """이 제출물 하나를 지정한 모델로 분석한다 (운영자 전용).

        같은 제출물을 여러 모델로 돌려 나란히 비교하는 것이 이 엔드포인트의 용도다.
        분석은 백그라운드 스레드에서 돌고 여기서는 '분석 중' 행만 돌려준다 — 워커가 1개라
        LLM 호출을 요청 안에서 기다리면 그동안 서비스 전체가 멈춘다.
        """
        submission = self.get_object()
        review = ensure_pending_review(
            submission,
            provider=request.data.get('provider') or None,
            model=request.data.get('model') or None,
        )
        if not review.provider:
            return Response(
                {'detail': '설정된 LLM API 키가 없습니다.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        run_in_background(analyze_submission, submission, review.provider, review.model)
        return Response(
            SubmissionReviewSerializer(review).data, status=status.HTTP_202_ACCEPTED
        )

    @action(detail=True, methods=['get'], permission_classes=[permissions.IsAuthenticated])
    def reviews(self, request, pk=None):
        """이 제출물의 분석 결과 전부 (운영자·배정된 심사위원 전용).

        참가자에게는 보이지 않는다 — 결선 점수와 같은 등급으로 다룬다. 자기 프로젝트에 대한
        평가를 참가자가 미리 보면 안 된다.
        """
        submission = self.get_object()
        contest = submission.team.contest
        if not (request.user.is_staff or is_contest_judge(contest, request.user)):
            raise PermissionDenied('심사위원과 운영자만 볼 수 있습니다.')
        reviews = submission.reviews.select_related('submission')
        return Response({'reviews': SubmissionReviewSerializer(reviews, many=True).data})


class JudgeViewSet(viewsets.ModelViewSet):
    serializer_class = JudgeSerializer
    permission_classes = [IsOrganizerOrReadOnly]
    # 배정은 만들고 없애는 것뿐이다. PUT/PATCH 로 user/contest 를 바꾸면 그 심사위원이 입력한
    # 점수가 통째로 다른 사람·다른 대회 것이 되므로 열어두지 않는다.
    http_method_names = ['get', 'post', 'delete', 'head', 'options']

    def get_queryset(self):
        queryset = Judge.objects.select_related('user', 'contest').annotate(score_count=Count('scores'))
        contest_slug = self.request.query_params.get('contest')
        if contest_slug:
            queryset = queryset.filter(contest__slug=contest_slug)
        return queryset

    def perform_destroy(self, instance):
        # Score.judge 가 CASCADE 라 해제하면 그 심사위원의 점수가 모두 지워지고 순위가 바뀐다.
        score_count = instance.scores.count()
        if score_count:
            raise PermissionDenied(
                f'이미 채점한 심사위원은 해제할 수 없습니다 (입력한 점수 {score_count}건).'
            )
        instance.delete()


class ScoreViewSet(viewsets.ModelViewSet):
    serializer_class = ScoreSerializer
    permission_classes = [IsAssignedJudge]

    SCORING_LOCKED_MESSAGE = '심사중 상태에서만 채점할 수 있습니다.'

    def get_queryset(self):
        queryset = Score.objects.select_related('submission', 'judge__user')
        params = self.request.query_params
        contest_slug = params.get('contest')
        if contest_slug:
            queryset = queryset.filter(submission__team__contest__slug=contest_slug)
        # 운영자(staff)는 기본적으로 모든 점수를 보지만, 채점 화면은 자기 점수만 필요하다.
        # ?mine=1 이 없으면 운영자가 심사위원을 겸할 때 남의 점수를 자기 것으로 알고 덮어쓴다.
        if not self.request.user.is_staff or params.get('mine') in ('1', 'true'):
            queryset = queryset.filter(judge__user=self.request.user)
        return queryset

    def perform_create(self, serializer):
        submission = serializer.validated_data['submission']
        contest = submission.team.contest
        judge = Judge.objects.filter(contest=contest, user=self.request.user).first()
        if judge is None:
            raise PermissionDenied('이 대회의 심사위원으로 등록되어 있지 않습니다.')
        ensure_contest_status(contest, SCORING_STATUSES, self.SCORING_LOCKED_MESSAGE)
        # 같은 심사위원이 같은 라운드에 다시 POST 하면 기존 점수를 덮어쓴다 (upsert).
        # unique_together 위반으로 500 이 나는 대신, 탭이 여러 개여도 안전하게 저장된다.
        # select_for_update 로 이미 있는 행은 잠가서 두 탭이 "수정"으로 겹치는 경우를 막는다.
        # 하지만 두 탭이 모두 "아직 없음"을 보고 동시에 새로 만들 때는 잠글 행 자체가 없으므로,
        # 그 경우에 unique_together 가 막아주는 IntegrityError 를 잡아 재시도해 upsert로 만든다.
        round_value = serializer.validated_data.get('round', Score.Round.PRELIMINARY)
        for attempt in range(2):
            try:
                with transaction.atomic():
                    existing = Score.objects.select_for_update().filter(
                        submission=submission, judge=judge, round=round_value,
                    ).first()
                    if existing is not None:
                        serializer.instance = existing
                    serializer.save(judge=judge)
                # 새 점수가 곧 새 순위다. 캐시를 비워야 다음 폴링이 바로 반영한다.
                invalidate_scoreboard(contest.pk)
                return
            except IntegrityError:
                if attempt == 1:
                    raise
                serializer.instance = None

    def perform_update(self, serializer):
        contest = serializer.instance.submission.team.contest
        ensure_contest_status(contest, SCORING_STATUSES, self.SCORING_LOCKED_MESSAGE)
        serializer.save()
        invalidate_scoreboard(contest.pk)

    def perform_destroy(self, instance):
        contest_id = instance.submission.team.contest_id
        instance.delete()
        invalidate_scoreboard(contest_id)


class AwardViewSet(viewsets.ModelViewSet):
    """rank → 상 이름(대상/최우수상/창의상 등) 설정. 시상식 화면이 최종 순위와 엮어 호명하는
    데 쓰므로, 종합 순위와 마찬가지로 발표 전까지는 운영자만 볼 수 있게 전체를 staff 전용으로
    막는다(IsOrganizer는 SAFE_METHODS 도 예외를 두지 않는다)."""

    serializer_class = AwardSerializer
    permission_classes = [IsOrganizer]
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        queryset = Award.objects.all()
        contest_slug = self.request.query_params.get('contest')
        if contest_slug:
            queryset = queryset.filter(contest__slug=contest_slug)
        return queryset


# ---------- 팀빌딩 (프로필 + 추천) ----------


class MyProfileView(generics.RetrieveUpdateAPIView):
    """내 팀빌딩 프로필. 없으면 만들어서 돌려준다(참가자가 먼저 생성할 일이 없게).

    남의 프로필은 여기로 볼 수 없다. 추천 응답에 필요한 만큼만 따로 노출된다.
    """

    serializer_class = ProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        profile, _ = Profile.objects.get_or_create(user=self.request.user)
        return profile

    def perform_update(self, serializer):
        profile = serializer.save()
        # 자기소개 원문이 바뀌면 기존 추출 결과는 더 이상 그 원문에서 나온 것이 아니다.
        # 지우지는 않는다 — 재추출 전까지는 옛 태그라도 있는 편이 추천에 낫다.
        if 'intro' in serializer.validated_data:
            profile.extraction_status = Profile.ExtractionStatus.PENDING
            profile.save(update_fields=['extraction_status'])


class ProfileExtractView(APIView):
    """자기소개 원문을 LLM 으로 구조화한다 (본인만).

    참가자당 1회짜리 짧은 호출이라 동기로 처리한다. 실패해도 400 을 내지 않고 프로필을 그대로
    돌려준다 — 추출 실패가 팀빌딩을 막으면 안 되고, 참가자가 태그를 직접 채울 수 있다.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        profile, _ = Profile.objects.get_or_create(user=request.user)
        profile = extract_profile(
            profile,
            provider=request.data.get('provider') or None,
            model=request.data.get('model') or None,
        )
        return Response(ProfileSerializer(profile).data)


class TechStackListView(APIView):
    """정규 기술 스택 목록. 프로필의 선택 UI 가 이걸 그대로 쓴다.

    목록은 백엔드가 정본이다 — 파이썬과 TypeScript 양쪽에 목록을 두면 반드시 어긋난다.
    프로필 화면이 열릴 때마다 읽히므로 캐시한다(낡아 봐야 방금 추가한 태그가 몇 분간 안 보이는
    정도라, 권한 판정 캐시를 보류한 이유와는 위험의 성격이 다르다).
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        cached = cache.get(tech_stacks.LIST_CACHE_KEY)
        if cached is None:
            cached = TechStackSerializer(
                TechStack.objects.filter(is_active=True), many=True
            ).data
            cache.set(tech_stacks.LIST_CACHE_KEY, cached, tech_stacks.CACHE_SECONDS)
        return Response({'stacks': cached})


class LlmModelsView(APIView):
    """키가 설정된 제공사 목록. 프론트의 모델 선택기가 이걸 그대로 쓴다."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response({'providers': available_models()})


class TeamRecommendationView(APIView):
    """이 대회에서 나에게 맞는 팀 순위. 순위는 전부 규칙 기반이라 LLM 키가 없어도 동작한다."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, slug):
        contest = get_object_or_404(Contest, slug=slug)
        return Response({'teams': recommend_teams_for_user(contest, request.user)})


class TeamCandidateView(APIView):
    """이 팀에 맞는, 아직 팀이 없는 사람 순위. 팀원과 운영자만 본다."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        team = get_object_or_404(Team.objects.select_related('contest'), pk=pk)
        is_member = Participant.objects.filter(team=team, user=request.user).exists()
        if not (request.user.is_staff or is_member):
            raise PermissionDenied('이 팀의 참가자만 후보를 볼 수 있습니다.')
        return Response({'candidates': recommend_users_for_team(team.contest, team)})
