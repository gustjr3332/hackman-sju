import hashlib
import json

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Avg, BooleanField, Case, Count, Exists, OuterRef, Prefetch, Value, When
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import generics, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from .models import Award, Contest, Judge, Participant, Score, Submission, Team
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
    RegisterSerializer,
    ScoreboardEntrySerializer,
    ScoreSerializer,
    SubmissionSerializer,
    TeamSerializer,
)

# 대회 상태별 허용 동작. 프론트엔드 `src/rules.ts`와 동일한 규칙을 유지한다.
TEAM_FORMATION_STATUSES = {Contest.Status.RECRUITING, Contest.Status.ONGOING}
SUBMISSION_STATUSES = {Contest.Status.RECRUITING, Contest.Status.ONGOING}
SCORING_STATUSES = {Contest.Status.JUDGING}


def ensure_contest_status(contest, allowed, message):
    """Raise 403 when the contest is not in one of the allowed statuses."""
    if contest.status not in allowed:
        label = contest.get_status_display()
        raise PermissionDenied(f'{message} (현재 상태: {label})')


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
            user
            and user.is_authenticated
            and (user.is_staff or Judge.objects.filter(contest=contest, user=user).exists())
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
        last, alphabetically. Re-running this reassigns every team's slot, so organizers
        can call it again after late drops/additions before presentations start.
        """
        contest = self.get_object()
        start_at_raw = request.data.get('start_at')
        if start_at_raw:
            start_at = parse_datetime(start_at_raw)
            if start_at is None:
                return Response(
                    {'start_at': ['시작 시각 형식이 올바르지 않습니다 (ISO 8601).']},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            start_at = timezone.now()

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

        contest.presentation_start_at = start_at
        contest.save(update_fields=['presentation_start_at'])
        return Response(ContestSerializer(contest, context=self.get_serializer_context()).data)


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
            '모집중 또는 진행중 상태에서만 팀을 만들 수 있습니다.',
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
            '모집중 또는 진행중 상태에서만 팀에 참가할 수 있습니다.',
        )
        participant, created = Participant.objects.get_or_create(team=team, user=request.user)
        if not created:
            return Response({'detail': '이미 참가 중인 팀입니다.'}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ParticipantSerializer(participant).data, status=status.HTTP_201_CREATED)


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
