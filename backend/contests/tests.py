import base64
import json
import urllib.error
from decimal import Decimal
from io import BytesIO
from unittest import mock
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Award, Contest, Judge, Score, Submission, Team

User = get_user_model()


class ApiTestCase(APITestCase):
    """모든 API 테스트의 베이스.

    스코어보드·GitHub 응답 캐시는 프로세스 메모리(LocMemCache)에 있어 DB 처럼 테스트마다
    롤백되지 않는다. 비우지 않으면 앞 테스트가 넣어둔 순위를 뒤 테스트가 그대로 받는다.
    `setUp` 대신 `_pre_setup` 에 거는 이유는, 하위 클래스가 `super().setUp()` 을 부르지
    않아도 항상 실행되기 때문이다.
    """

    def _pre_setup(self):
        super()._pre_setup()
        cache.clear()


def make_contest(slug='hack-2026', **kwargs):
    now = timezone.now()
    defaults = {
        'slug': slug,
        'name': '2026 교내 해커톤',
        'start_at': now,
        'end_at': now + timezone.timedelta(days=1),
    }
    defaults.update(kwargs)
    return Contest.objects.create(**defaults)


class AuthFlowTests(ApiTestCase):
    def test_register_and_login(self):
        res = self.client.post('/api/auth/register/', {
            'username': 'alice', 'email': 'alice@example.com', 'password': 'strongpass123',
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        res = self.client.post('/api/auth/token/', {
            'username': 'alice', 'password': 'strongpass123',
        })
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('access', res.data)

    def test_login_with_wrong_password_is_rejected(self):
        User.objects.create_user('bob', password='strongpass123')
        res = self.client.post('/api/auth/token/', {'username': 'bob', 'password': 'wrong-password'})
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_refresh_token_issues_new_access_token(self):
        User.objects.create_user('carol', password='strongpass123')
        tokens = self.client.post('/api/auth/token/', {
            'username': 'carol', 'password': 'strongpass123',
        }).data

        res = self.client.post('/api/auth/token/refresh/', {'refresh': tokens['refresh']})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('access', res.data)

        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {res.data["access"]}')
        me = self.client.get('/api/auth/me/')
        self.assertEqual(me.status_code, status.HTTP_200_OK)
        self.assertEqual(me.data['username'], 'carol')

    def test_expired_or_garbage_access_token_returns_401(self):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer not-a-real-token')
        res = self.client.get('/api/auth/me/')
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)


class ContestApiTests(ApiTestCase):
    def setUp(self):
        self.organizer = User.objects.create_user('organizer', password='pw12345678', is_staff=True)
        self.participant = User.objects.create_user('participant', password='pw12345678')
        self.contest = make_contest()

    def test_anonymous_can_list_contests(self):
        res = self.client.get('/api/contests/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)

    def test_anonymous_cannot_create_contest(self):
        res = self.client.post('/api/contests/', {
            'slug': 'x', 'name': 'X', 'start_at': timezone.now(), 'end_at': timezone.now(),
        })
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_organizer_can_create_contest(self):
        self.client.force_authenticate(self.organizer)
        res = self.client.post('/api/contests/', {
            'slug': 'hack-2027', 'name': '2027 해커톤',
            'start_at': timezone.now(), 'end_at': timezone.now(),
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['status'], 'recruiting')

    def test_contest_end_before_start_is_rejected(self):
        self.client.force_authenticate(self.organizer)
        now = timezone.now()
        res = self.client.post('/api/contests/', {
            'slug': 'hack-bad', 'name': '거꾸로 대회',
            'start_at': now, 'end_at': now - timezone.timedelta(hours=1),
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('end_at', res.data)

    def test_authenticated_user_can_create_team_and_is_auto_joined(self):
        self.client.force_authenticate(self.participant)
        res = self.client.post('/api/teams/', {'contest': self.contest.slug, 'name': '팀 A'})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        team = Team.objects.get(pk=res.data['id'])
        self.assertTrue(team.participants.filter(user=self.participant).exists())

    def test_only_team_member_can_submit(self):
        self.client.force_authenticate(self.participant)
        team = Team.objects.create(contest=self.contest, name='팀 B')
        team.participants.create(user=self.participant)

        other = User.objects.create_user('outsider', password='pw12345678')

        res = self.client.post('/api/submissions/', {
            'team': team.id, 'title': '제출물', 'description': '', 'link_url': '',
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        self.client.force_authenticate(other)
        res = self.client.patch(f'/api/submissions/{res.data["id"]}/', {'title': '변경'})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_outsider_cannot_create_submission_for_another_team(self):
        team = Team.objects.create(contest=self.contest, name='팀 C')
        team.participants.create(user=self.participant)
        outsider = User.objects.create_user('outsider', password='pw12345678')

        self.client.force_authenticate(outsider)
        res = self.client.post('/api/submissions/', {
            'team': team.id, 'title': '남의 팀에 제출', 'description': '', 'link_url': '',
        })
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Submission.objects.filter(team=team).exists())


class ContestStatusTransitionTests(ApiTestCase):
    def setUp(self):
        self.organizer = User.objects.create_user('organizer', password='pw12345678', is_staff=True)
        self.participant = User.objects.create_user('participant', password='pw12345678')
        self.contest = make_contest()

    def test_organizer_can_advance_status(self):
        self.client.force_authenticate(self.organizer)
        for next_status in ['ongoing', 'judging', 'closed']:
            res = self.client.patch(f'/api/contests/{self.contest.slug}/', {'status': next_status})
            self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
            self.contest.refresh_from_db()
            self.assertEqual(self.contest.status, next_status)

    def test_non_organizer_cannot_change_status(self):
        self.client.force_authenticate(self.participant)
        res = self.client.patch(f'/api/contests/{self.contest.slug}/', {'status': 'closed'})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.contest.refresh_from_db()
        self.assertEqual(self.contest.status, 'recruiting')

    def test_unknown_status_value_is_rejected(self):
        self.client.force_authenticate(self.organizer)
        res = self.client.patch(f'/api/contests/{self.contest.slug}/', {'status': 'paused'})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)


class StatusGatingTests(ApiTestCase):
    """대회 상태(모집중 → 진행중 → 심사중 → 종료)에 따라 허용되는 동작이 달라진다."""

    def setUp(self):
        self.participant = User.objects.create_user('participant', password='pw12345678')
        self.judge_user = User.objects.create_user('judge1', password='pw12345678')
        self.contest = make_contest()
        self.team = Team.objects.create(contest=self.contest, name='팀 A')
        self.team.participants.create(user=self.participant)
        self.judge = Judge.objects.create(contest=self.contest, user=self.judge_user)

    def set_status(self, value):
        self.contest.status = value
        self.contest.save(update_fields=['status'])

    def test_team_can_be_created_while_ongoing(self):
        self.set_status(Contest.Status.ONGOING)
        self.client.force_authenticate(self.participant)
        res = self.client.post('/api/teams/', {'contest': self.contest.slug, 'name': '팀 B'})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_team_cannot_be_created_while_judging(self):
        self.set_status(Contest.Status.JUDGING)
        self.client.force_authenticate(self.participant)
        res = self.client.post('/api/teams/', {'contest': self.contest.slug, 'name': '팀 B'})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn('심사중', res.data['detail'])

    def test_cannot_join_team_after_close(self):
        self.set_status(Contest.Status.CLOSED)
        latecomer = User.objects.create_user('latecomer', password='pw12345678')
        self.client.force_authenticate(latecomer)
        res = self.client.post(f'/api/teams/{self.team.id}/join/')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(self.team.participants.filter(user=latecomer).exists())

    def test_submission_is_locked_once_judging_starts(self):
        submission = Submission.objects.create(team=self.team, title='초안')
        self.set_status(Contest.Status.JUDGING)
        self.client.force_authenticate(self.participant)
        res = self.client.patch(f'/api/submissions/{submission.id}/', {'title': '심사 중 수정'})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        submission.refresh_from_db()
        self.assertEqual(submission.title, '초안')

    def test_submission_cannot_be_created_after_close(self):
        self.set_status(Contest.Status.CLOSED)
        self.client.force_authenticate(self.participant)
        res = self.client.post('/api/submissions/', {
            'team': self.team.id, 'title': '늦은 제출', 'description': '', 'link_url': '',
        })
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_judge_cannot_score_before_judging_phase(self):
        Submission.objects.create(team=self.team, title='제출물 A')
        self.set_status(Contest.Status.ONGOING)
        self.client.force_authenticate(self.judge_user)
        res = self.client.post('/api/scores/', {
            'submission': self.team.submission.id, 'round': 'preliminary', 'value': '9',
        })
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(Score.objects.count(), 0)

    def test_judge_can_score_during_judging_phase(self):
        Submission.objects.create(team=self.team, title='제출물 A')
        self.set_status(Contest.Status.JUDGING)
        self.client.force_authenticate(self.judge_user)
        res = self.client.post('/api/scores/', {
            'submission': self.team.submission.id, 'round': 'preliminary', 'value': '9',
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_score_cannot_be_changed_after_close(self):
        submission = Submission.objects.create(team=self.team, title='제출물 A')
        score = Score.objects.create(submission=submission, judge=self.judge, round='final', value='7')
        self.set_status(Contest.Status.CLOSED)
        self.client.force_authenticate(self.judge_user)
        res = self.client.patch(f'/api/scores/{score.id}/', {'value': '10'})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        score.refresh_from_db()
        self.assertEqual(score.value, Decimal('7'))


class MeAndJudgeAssignmentTests(ApiTestCase):
    def setUp(self):
        self.organizer = User.objects.create_user('organizer', password='pw12345678', is_staff=True)
        self.participant = User.objects.create_user('participant', password='pw12345678')
        self.contest = make_contest()

    def test_me_reflects_organizer_status(self):
        self.client.force_authenticate(self.organizer)
        res = self.client.get('/api/auth/me/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data, {'username': 'organizer', 'is_staff': True})

    def test_me_requires_authentication(self):
        res = self.client.get('/api/auth/me/')
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_organizer_can_assign_judge_by_username(self):
        self.client.force_authenticate(self.organizer)
        res = self.client.post('/api/judges/', {
            'contest': self.contest.slug, 'username': 'participant',
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(Judge.objects.filter(contest=self.contest, user=self.participant).exists())

    def test_assigning_unknown_username_fails(self):
        self.client.force_authenticate(self.organizer)
        res = self.client.post('/api/judges/', {
            'contest': self.contest.slug, 'username': 'nobody',
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_non_organizer_cannot_assign_judge(self):
        self.client.force_authenticate(self.participant)
        res = self.client.post('/api/judges/', {
            'contest': self.contest.slug, 'username': 'participant',
        })
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_organizer_can_remove_judge(self):
        judge = Judge.objects.create(contest=self.contest, user=self.participant)
        self.client.force_authenticate(self.organizer)
        res = self.client.delete(f'/api/judges/{judge.id}/')
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Judge.objects.filter(pk=judge.id).exists())

    def test_removing_a_judge_who_has_scored_is_blocked(self):
        judge = Judge.objects.create(contest=self.contest, user=self.participant)
        team = Team.objects.create(contest=self.contest, name='팀 A')
        submission = Submission.objects.create(team=team, title='제출물 A')
        Score.objects.create(submission=submission, judge=judge, round='preliminary', value='9')

        self.client.force_authenticate(self.organizer)
        res = self.client.delete(f'/api/judges/{judge.id}/')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Judge.objects.filter(pk=judge.id).exists())
        self.assertEqual(Score.objects.filter(judge=judge).count(), 1)

    def test_duplicate_judge_assignment_gives_korean_message(self):
        Judge.objects.create(contest=self.contest, user=self.participant)
        self.client.force_authenticate(self.organizer)
        res = self.client.post('/api/judges/', {
            'contest': self.contest.slug, 'username': 'participant',
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        message = ''.join(str(v) for v in res.data.get('non_field_errors', res.data.values()))
        self.assertIn('이미', message)

    def test_judge_patch_is_not_allowed(self):
        judge = Judge.objects.create(contest=self.contest, user=self.participant)
        self.client.force_authenticate(self.organizer)
        res = self.client.patch(f'/api/judges/{judge.id}/', {'username': 'organizer'})
        self.assertEqual(res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_contest_list_reports_is_judge_for_assigned_user(self):
        Judge.objects.create(contest=self.contest, user=self.participant)
        self.client.force_authenticate(self.participant)
        res = self.client.get('/api/contests/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        entry = next(c for c in res.data if c['slug'] == self.contest.slug)
        self.assertTrue(entry['is_judge'])

    def test_contest_list_reports_is_judge_false_for_non_judge(self):
        self.client.force_authenticate(self.participant)
        res = self.client.get('/api/contests/')
        entry = next(c for c in res.data if c['slug'] == self.contest.slug)
        self.assertFalse(entry['is_judge'])

    def test_anonymous_contest_list_is_judge_false(self):
        res = self.client.get('/api/contests/')
        entry = next(c for c in res.data if c['slug'] == self.contest.slug)
        self.assertFalse(entry['is_judge'])


class ContestListQueryCountTests(ApiTestCase):
    """team_count/is_judge 는 annotate 로 얻으므로 대회 수가 늘어도 쿼리 수는 고정이다."""

    def setUp(self):
        self.user = User.objects.create_user('watcher', password='pw12345678')

    def make_contests(self, n):
        now = timezone.now()
        for i in range(n):
            contest = Contest.objects.create(
                slug=f'q-{i}', name=f'대회 {i}', start_at=now, end_at=now
            )
            team = Team.objects.create(contest=contest, name='팀')
            Judge.objects.create(contest=contest, user=self.user)
            self.judge_for_count = Judge.objects.filter(contest=contest, user=self.user).first()

    def test_query_count_is_independent_of_contest_count(self):
        self.client.force_authenticate(self.user)
        self.make_contests(2)
        with self.assertNumQueries(1):
            res = self.client.get('/api/contests/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        baseline = len(res.data)

        Contest.objects.all().delete()
        self.make_contests(6)
        with self.assertNumQueries(1):
            res = self.client.get('/api/contests/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), baseline + 4)
        self.assertTrue(all(entry['is_judge'] for entry in res.data))


class TeamListQueryCountTests(ApiTestCase):
    """참가자 username 을 prefetch 하므로 팀/참가자 수가 늘어도 쿼리 수는 고정이다."""

    def setUp(self):
        self.contest = make_contest()
        self._counter = 0

    def make_teams_with_participants(self, n):
        for _ in range(n):
            self._counter += 1
            i = self._counter
            team = Team.objects.create(contest=self.contest, name=f'팀 {i}')
            user = User.objects.create_user(f'member{i}', password='pw12345678')
            team.participants.create(user=user)

    def test_query_count_is_independent_of_team_count(self):
        self.make_teams_with_participants(2)
        with self.assertNumQueries(2):
            res = self.client.get(f'/api/teams/?contest={self.contest.slug}')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        baseline = len(res.data)

        self.make_teams_with_participants(6)
        with self.assertNumQueries(2):
            res = self.client.get(f'/api/teams/?contest={self.contest.slug}')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), baseline + 6)


class ScoreboardTests(ApiTestCase):
    def setUp(self):
        self.contest = make_contest(status=Contest.Status.JUDGING)
        self.team = Team.objects.create(contest=self.contest, name='팀 A')
        self.submission = Submission.objects.create(team=self.team, title='제출물 A')
        self.judge_user = User.objects.create_user('judge1', password='pw12345678')
        self.judge = Judge.objects.create(contest=self.contest, user=self.judge_user)

    def test_staff_scores_endpoint_returns_all_by_default(self):
        another_judge_user = User.objects.create_user('judge2', password='pw12345678')
        another_judge = Judge.objects.create(contest=self.contest, user=another_judge_user)
        Score.objects.create(submission=self.submission, judge=self.judge, round='preliminary', value='9')
        Score.objects.create(submission=self.submission, judge=another_judge, round='preliminary', value='7')

        staff = User.objects.create_user('staffer', password='pw12345678', is_staff=True)
        self.client.force_authenticate(staff)
        res = self.client.get('/api/scores/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 2)

    def test_staff_scores_mine_filter_excludes_other_judges(self):
        another_judge_user = User.objects.create_user('judge2', password='pw12345678')
        another_judge = Judge.objects.create(contest=self.contest, user=another_judge_user)
        Score.objects.create(submission=self.submission, judge=self.judge, round='preliminary', value='9')
        Score.objects.create(submission=self.submission, judge=another_judge, round='preliminary', value='7')

        staff_judge = User.objects.create_user('staffjudge', password='pw12345678', is_staff=True)
        Judge.objects.create(contest=self.contest, user=staff_judge)
        self.client.force_authenticate(staff_judge)
        res = self.client.get(f'/api/scores/?contest={self.contest.slug}&mine=1')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data, [])

    def test_non_judge_cannot_score(self):
        outsider = User.objects.create_user('outsider', password='pw12345678')
        self.client.force_authenticate(outsider)
        res = self.client.post('/api/scores/', {
            'submission': self.submission.id, 'round': 'preliminary', 'value': '9.5',
        })
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_assigned_judge_can_score_and_scoreboard_aggregates(self):
        self.client.force_authenticate(self.judge_user)
        res = self.client.post('/api/scores/', {
            'submission': self.submission.id, 'round': 'preliminary', 'value': '9.5',
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        res = self.client.get(f'/api/contests/{self.contest.slug}/scoreboard/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        prelim = next(e for e in res.data if e['round'] == 'preliminary')
        self.assertEqual(prelim['team_name'], '팀 A')
        self.assertEqual(str(prelim['average_score']), '9.50')
        self.assertEqual(prelim['vote_count'], 1)
        self.assertEqual(prelim['rank'], 1)

    def test_score_create_ignores_client_supplied_judge(self):
        another_judge_user = User.objects.create_user('judge2', password='pw12345678')
        Judge.objects.create(contest=self.contest, user=another_judge_user)

        self.client.force_authenticate(self.judge_user)
        res = self.client.post('/api/scores/', {
            'submission': self.submission.id, 'round': 'final', 'value': '8',
            'judge': Judge.objects.get(user=another_judge_user).id,
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        score = Score.objects.get(pk=res.data['id'])
        self.assertEqual(score.judge.user, self.judge_user)

    def test_posting_same_round_again_updates_existing_score(self):
        self.client.force_authenticate(self.judge_user)
        first = self.client.post('/api/scores/', {
            'submission': self.submission.id, 'round': 'preliminary', 'value': '9', 'comment': '초안',
        })
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)

        second = self.client.post('/api/scores/', {
            'submission': self.submission.id, 'round': 'preliminary', 'value': '7.5', 'comment': '수정',
        })
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.data['id'], first.data['id'])
        self.assertEqual(Score.objects.count(), 1)
        score = Score.objects.get()
        self.assertEqual(score.value, Decimal('7.5'))
        self.assertEqual(score.comment, '수정')


class ScoreboardRankingTests(ApiTestCase):
    """스코어보드는 라운드별로 평균 점수 순위를 매기고, 동점은 같은 순위를 공유한다."""

    def setUp(self):
        self.contest = make_contest(status=Contest.Status.JUDGING)
        self.judges = []
        for i in range(2):
            user = User.objects.create_user(f'judge{i}', password='pw12345678')
            self.judges.append(Judge.objects.create(contest=self.contest, user=user))

    def make_scored_team(self, name, prelim_values, final_values=()):
        team = Team.objects.create(contest=self.contest, name=name)
        submission = Submission.objects.create(team=team, title=f'{name} 제출물')
        for judge, value in zip(self.judges, prelim_values):
            Score.objects.create(submission=submission, judge=judge, round='preliminary', value=value)
        for judge, value in zip(self.judges, final_values):
            Score.objects.create(submission=submission, judge=judge, round='final', value=value)
        return team

    def board(self, round_value):
        res = self.client.get(f'/api/contests/{self.contest.slug}/scoreboard/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        return [e for e in res.data if e['round'] == round_value]

    def test_teams_are_ranked_by_average_descending(self):
        self.make_scored_team('팀 낮음', ['7', '8'])       # avg 7.50
        self.make_scored_team('팀 높음', ['9', '10'])      # avg 9.50
        self.make_scored_team('팀 중간', ['8', '9'])       # avg 8.50

        prelim = self.board('preliminary')
        self.assertEqual([e['team_name'] for e in prelim], ['팀 높음', '팀 중간', '팀 낮음'])
        self.assertEqual([e['rank'] for e in prelim], [1, 2, 3])
        self.assertEqual([str(e['average_score']) for e in prelim], ['9.50', '8.50', '7.50'])
        self.assertTrue(all(e['vote_count'] == 2 for e in prelim))

    def test_tied_averages_share_rank_and_skip_next(self):
        self.make_scored_team('팀 A', ['9', '9'])   # 9.00
        self.make_scored_team('팀 B', ['8', '10'])  # 9.00
        self.make_scored_team('팀 C', ['5', '6'])   # 5.50

        prelim = self.board('preliminary')
        ranks = {e['team_name']: e['rank'] for e in prelim}
        self.assertEqual(ranks['팀 A'], 1)
        self.assertEqual(ranks['팀 B'], 1)
        self.assertEqual(ranks['팀 C'], 3)

    def test_unscored_teams_come_last_with_null_rank(self):
        self.make_scored_team('팀 점수있음', ['8', '8'])
        Team.objects.create(contest=self.contest, name='팀 미제출')
        no_score_team = Team.objects.create(contest=self.contest, name='팀 제출만')
        Submission.objects.create(team=no_score_team, title='아직 미채점')

        prelim = self.board('preliminary')
        self.assertEqual(prelim[0]['team_name'], '팀 점수있음')
        self.assertEqual(prelim[0]['rank'], 1)
        tail = prelim[1:]
        self.assertEqual({e['team_name'] for e in tail}, {'팀 미제출', '팀 제출만'})
        self.assertTrue(all(e['rank'] is None for e in tail))
        self.assertTrue(all(e['average_score'] is None for e in tail))
        self.assertTrue(all(e['vote_count'] == 0 for e in tail))

    def test_rounds_are_ranked_independently(self):
        self.make_scored_team('팀 예선강자', ['10', '10'], final_values=['6', '6'])
        self.make_scored_team('팀 결선강자', ['7', '7'], final_values=['9', '9'])

        # final(종합) 순위는 공개되지 않으므로 심사위원으로 조회한다.
        self.client.force_authenticate(self.judges[0].user)
        prelim = self.board('preliminary')
        final = self.board('final')
        self.assertEqual(prelim[0]['team_name'], '팀 예선강자')
        self.assertEqual(final[0]['team_name'], '팀 결선강자')
        self.assertEqual(final[0]['rank'], 1)
        self.assertEqual(final[1]['rank'], 2)

    def test_scoreboard_lists_every_team_in_every_round_for_judge(self):
        self.make_scored_team('팀 A', ['9'])
        Team.objects.create(contest=self.contest, name='팀 B')

        self.client.force_authenticate(self.judges[0].user)
        res = self.client.get(f'/api/contests/{self.contest.slug}/scoreboard/')
        self.assertEqual(len(res.data), 4)  # 2 teams x 2 rounds
        rounds = [e['round'] for e in res.data]
        self.assertEqual(rounds, ['preliminary', 'preliminary', 'final', 'final'])


class ScoreboardPrivacyTests(ApiTestCase):
    """예선(코드/기능) 점수는 항상 공개, 결선(발표 포함 종합) 점수는 시상 전까지 비공개."""

    def setUp(self):
        self.contest = make_contest(status=Contest.Status.JUDGING)
        self.team = Team.objects.create(contest=self.contest, name='팀 A')
        submission = Submission.objects.create(team=self.team, title='제출물 A')
        self.judge_user = User.objects.create_user('judge1', password='pw12345678')
        judge = Judge.objects.create(contest=self.contest, user=self.judge_user)
        self.staff = User.objects.create_user('staffer', password='pw12345678', is_staff=True)
        self.outsider = User.objects.create_user('outsider', password='pw12345678')
        Score.objects.create(submission=submission, judge=judge, round='preliminary', value='9')
        Score.objects.create(submission=submission, judge=judge, round='final', value='8')

    def rounds_in(self, res):
        return {e['round'] for e in res.data}

    def test_anonymous_sees_only_preliminary(self):
        res = self.client.get(f'/api/contests/{self.contest.slug}/scoreboard/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(self.rounds_in(res), {'preliminary'})

    def test_logged_in_non_judge_sees_only_preliminary(self):
        self.client.force_authenticate(self.outsider)
        res = self.client.get(f'/api/contests/{self.contest.slug}/scoreboard/')
        self.assertEqual(self.rounds_in(res), {'preliminary'})

    def test_assigned_judge_sees_both_rounds(self):
        self.client.force_authenticate(self.judge_user)
        res = self.client.get(f'/api/contests/{self.contest.slug}/scoreboard/')
        self.assertEqual(self.rounds_in(res), {'preliminary', 'final'})

    def test_staff_sees_both_rounds(self):
        self.client.force_authenticate(self.staff)
        res = self.client.get(f'/api/contests/{self.contest.slug}/scoreboard/')
        self.assertEqual(self.rounds_in(res), {'preliminary', 'final'})


class PresentationScheduleTests(ApiTestCase):
    def setUp(self):
        self.organizer = User.objects.create_user('organizer', password='pw12345678', is_staff=True)
        self.participant = User.objects.create_user('participant', password='pw12345678')
        self.contest = make_contest()
        self.team_no_submission = Team.objects.create(contest=self.contest, name='팀 나중')
        self.team_with_submission = Team.objects.create(contest=self.contest, name='팀 먼저')
        Submission.objects.create(team=self.team_with_submission, title='제출물')

    def test_organizer_can_assign_presentation_order(self):
        self.client.force_authenticate(self.organizer)
        res = self.client.post(
            f'/api/contests/{self.contest.slug}/assign_presentation_order/',
            {'start_at': '2026-09-05T10:00:00Z'},
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.contest.refresh_from_db()
        self.assertIsNotNone(self.contest.presentation_start_at)

        self.team_with_submission.refresh_from_db()
        self.team_no_submission.refresh_from_db()
        # 제출한 팀이 먼저, 미제출 팀은 뒤로.
        self.assertEqual(self.team_with_submission.presentation_order, 1)
        self.assertEqual(self.team_no_submission.presentation_order, 2)

    def test_non_organizer_cannot_assign_presentation_order(self):
        self.client.force_authenticate(self.participant)
        res = self.client.post(f'/api/contests/{self.contest.slug}/assign_presentation_order/')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.team_with_submission.refresh_from_db()
        self.assertIsNone(self.team_with_submission.presentation_order)

    def test_team_serializer_exposes_computed_slot_times(self):
        self.client.force_authenticate(self.organizer)
        self.client.post(
            f'/api/contests/{self.contest.slug}/assign_presentation_order/',
            {'start_at': '2026-09-05T10:00:00Z'},
        )
        res = self.client.get(f'/api/teams/?contest={self.contest.slug}')
        by_id = {t['id']: t for t in res.data}
        first = by_id[self.team_with_submission.id]
        second = by_id[self.team_no_submission.id]
        self.assertEqual(first['presentation_starts_at'], '2026-09-05T10:00:00Z')
        self.assertEqual(first['presentation_ends_at'], '2026-09-05T10:10:00Z')
        self.assertEqual(second['presentation_starts_at'], '2026-09-05T10:10:00Z')

    def test_invalid_start_at_is_rejected(self):
        self.client.force_authenticate(self.organizer)
        res = self.client.post(
            f'/api/contests/{self.contest.slug}/assign_presentation_order/',
            {'start_at': 'not-a-date'},
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)


class AwardApiTests(ApiTestCase):
    def setUp(self):
        self.organizer = User.objects.create_user('organizer', password='pw12345678', is_staff=True)
        self.participant = User.objects.create_user('participant', password='pw12345678')
        self.contest = make_contest()

    def test_organizer_can_create_award(self):
        self.client.force_authenticate(self.organizer)
        res = self.client.post('/api/awards/', {
            'contest': self.contest.slug, 'rank': 1, 'title': '대상',
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(Award.objects.filter(contest=self.contest, rank=1, title='대상').exists())

    def test_duplicate_rank_is_rejected(self):
        Award.objects.create(contest=self.contest, rank=1, title='대상')
        self.client.force_authenticate(self.organizer)
        res = self.client.post('/api/awards/', {
            'contest': self.contest.slug, 'rank': 1, 'title': '최우수상',
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_non_organizer_cannot_read_awards(self):
        Award.objects.create(contest=self.contest, rank=1, title='대상')
        self.client.force_authenticate(self.participant)
        res = self.client.get(f'/api/awards/?contest={self.contest.slug}')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_cannot_read_awards(self):
        Award.objects.create(contest=self.contest, rank=1, title='대상')
        res = self.client.get(f'/api/awards/?contest={self.contest.slug}')
        # 인증 정보 자체가 없으므로 DRF 관례상 403이 아니라 401(다른 organizer-only 엔드포인트와 동일).
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_organizer_can_update_and_delete_award(self):
        award = Award.objects.create(contest=self.contest, rank=2, title='우수상')
        self.client.force_authenticate(self.organizer)
        res = self.client.patch(f'/api/awards/{award.id}/', {'title': '최우수상'})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        award.refresh_from_db()
        self.assertEqual(award.title, '최우수상')

        res = self.client.delete(f'/api/awards/{award.id}/')
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Award.objects.filter(pk=award.id).exists())


def fake_github_response(payload):
    """`urllib.request.urlopen` 이 돌려주는 컨텍스트 매니저 흉내 (JSON 본문만 필요하다)."""
    body = BytesIO(json.dumps(payload).encode('utf-8'))
    fake = mock.MagicMock()
    fake.__enter__.return_value = body
    fake.__exit__.return_value = False
    return fake


class ScoreboardCachingTests(ApiTestCase):
    """스코어보드는 몇 초 캐시 + ETag 로 폴링 부담을 줄이되, 쓰기 직후에는 반드시 최신이다."""

    def setUp(self):
        self.contest = make_contest(status=Contest.Status.JUDGING)
        self.team = Team.objects.create(contest=self.contest, name='팀 A')
        self.submission = Submission.objects.create(team=self.team, title='제출물 A')
        self.judge_user = User.objects.create_user('judge1', password='pw12345678')
        self.judge = Judge.objects.create(contest=self.contest, user=self.judge_user)
        self.url = f'/api/contests/{self.contest.slug}/scoreboard/'

    def score(self, value, round_value='preliminary'):
        return Score.objects.create(
            submission=self.submission, judge=self.judge, round=round_value, value=value,
        )

    def test_repeat_request_is_served_from_cache_without_aggregating_again(self):
        self.score('9')
        first = self.client.get(self.url)
        self.assertEqual(first.status_code, status.HTTP_200_OK)

        # 캐시 적중이면 팀/집계 쿼리가 사라지고 대회 조회 한 번만 남는다.
        with self.assertNumQueries(1):
            second = self.client.get(self.url)
        self.assertEqual(second.data, first.data)
        self.assertEqual(second['ETag'], first['ETag'])

    def test_unchanged_board_answers_304_with_no_body(self):
        self.score('9')
        first = self.client.get(self.url)
        etag = first['ETag']

        second = self.client.get(self.url, HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(second.status_code, status.HTTP_304_NOT_MODIFIED)
        self.assertEqual(second.content, b'')
        self.assertEqual(second['ETag'], etag)

    def test_public_and_judge_boards_do_not_share_a_cache_entry(self):
        self.score('8', round_value='final')

        public = self.client.get(self.url)
        self.assertEqual({e['round'] for e in public.data}, {'preliminary'})

        self.client.force_authenticate(self.judge_user)
        judged = self.client.get(self.url)
        self.assertEqual({e['round'] for e in judged.data}, {'preliminary', 'final'})
        self.assertNotEqual(public['ETag'], judged['ETag'])

    def test_new_score_invalidates_cache_immediately(self):
        before = self.client.get(self.url)
        self.assertIsNone(before.data[0]['average_score'])

        self.client.force_authenticate(self.judge_user)
        res = self.client.post('/api/scores/', {
            'submission': self.submission.id, 'round': 'preliminary', 'value': '9.5',
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)

        self.client.force_authenticate(None)
        after = self.client.get(self.url)
        self.assertEqual(str(after.data[0]['average_score']), '9.50')
        self.assertNotEqual(after['ETag'], before['ETag'])

    def test_new_team_invalidates_cache_immediately(self):
        self.client.get(self.url)  # 캐시 채우기

        Contest.objects.filter(pk=self.contest.pk).update(status=Contest.Status.RECRUITING)
        joiner = User.objects.create_user('joiner', password='pw12345678')
        self.client.force_authenticate(joiner)
        res = self.client.post('/api/teams/', {'contest': self.contest.slug, 'name': '팀 B'})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)

        self.client.force_authenticate(None)
        after = self.client.get(self.url)
        self.assertEqual({e['team_name'] for e in after.data}, {'팀 A', '팀 B'})


class GithubProxyTests(ApiTestCase):
    """심사 도구의 GitHub 프록시 — 저장소 URL 만 받고, 로그인한 사용자만 쓸 수 있다."""

    def setUp(self):
        self.user = User.objects.create_user('judge1', password='pw12345678')
        self.repo = 'https://github.com/octocat/Hello-World'

    def get(self, resource, **params):
        query = urlencode(params)
        return self.client.get(f'/api/github/{resource}/?{query}')

    def test_anonymous_is_rejected(self):
        res = self.get('repo', repo=self.repo)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_non_github_url_is_rejected_without_calling_out(self):
        self.client.force_authenticate(self.user)
        with mock.patch('contests.github.urllib.request.urlopen') as urlopen:
            res = self.get('repo', repo='https://gitlab.com/foo/bar')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        urlopen.assert_not_called()

    def test_repo_returns_default_branch_and_caches_the_upstream_call(self):
        self.client.force_authenticate(self.user)
        with mock.patch('contests.github.urllib.request.urlopen') as urlopen:
            urlopen.return_value = fake_github_response({'default_branch': 'develop'})
            first = self.get('repo', repo=self.repo)
            second = self.get('repo', repo=self.repo)

        self.assertEqual(first.data, {'default_branch': 'develop'})
        self.assertEqual(second.data, {'default_branch': 'develop'})
        # 두 번째 요청은 캐시에서 나가므로 GitHub 호출은 한 번뿐이다.
        self.assertEqual(urlopen.call_count, 1)

    def test_token_is_sent_to_github_but_never_to_the_client(self):
        self.client.force_authenticate(self.user)
        with self.settings(GITHUB_TOKEN='ghp_secret'):
            with mock.patch('contests.github.urllib.request.urlopen') as urlopen:
                urlopen.return_value = fake_github_response({'default_branch': 'main'})
                res = self.get('repo', repo=self.repo)
                sent = urlopen.call_args[0][0]
        self.assertEqual(sent.get_header('Authorization'), 'Bearer ghp_secret')
        self.assertNotIn('ghp_secret', res.content.decode())

    def test_tree_returns_blobs_only(self):
        self.client.force_authenticate(self.user)
        payload = {
            'truncated': False,
            'tree': [
                {'path': 'src', 'type': 'tree'},
                {'path': 'src/main.py', 'type': 'blob'},
                {'path': 'README.md', 'type': 'blob'},
            ],
        }
        with mock.patch('contests.github.urllib.request.urlopen') as urlopen:
            urlopen.return_value = fake_github_response(payload)
            res = self.get('tree', repo=self.repo, branch='main')
        self.assertEqual([f['path'] for f in res.data['files']], ['src/main.py', 'README.md'])
        self.assertFalse(res.data['truncated'])

    def test_file_content_is_base64_decoded(self):
        self.client.force_authenticate(self.user)
        source = 'print("안녕")'
        payload = {'content': base64.b64encode(source.encode()).decode(), 'encoding': 'base64'}
        with mock.patch('contests.github.urllib.request.urlopen') as urlopen:
            urlopen.return_value = fake_github_response(payload)
            res = self.get('file', repo=self.repo, path='src/main.py')
        self.assertEqual(res.data['content'], source)

    def test_path_traversal_is_refused(self):
        self.client.force_authenticate(self.user)
        with mock.patch('contests.github.urllib.request.urlopen') as urlopen:
            res = self.get('file', repo=self.repo, path='../../../user/repos')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        urlopen.assert_not_called()

    def test_rate_limit_is_reported_as_such(self):
        self.client.force_authenticate(self.user)
        error = urllib.error.HTTPError(url='x', code=403, msg='rate limited', hdrs=None, fp=None)
        with mock.patch('contests.github.urllib.request.urlopen', side_effect=error):
            res = self.get('repo', repo=self.repo)
        self.assertEqual(res.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(res.data['kind'], 'rate-limit')

    def test_missing_repo_is_reported_as_not_found(self):
        self.client.force_authenticate(self.user)
        error = urllib.error.HTTPError(url='x', code=404, msg='not found', hdrs=None, fp=None)
        with mock.patch('contests.github.urllib.request.urlopen', side_effect=error):
            res = self.get('repo', repo=self.repo)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(res.data['kind'], 'not-found')
