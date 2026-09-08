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

from .models import Award, Contest, Judge, Participant, Profile, Score, Submission, Team

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


class ContestDeleteTests(ApiTestCase):
    """대회 삭제는 운영자만, 그리고 딸린 데이터를 전부 함께 지운다.

    되돌릴 수 없는 유일한 파괴적 동작이라, 권한 경계와 연쇄 삭제 범위를 테스트로 못박는다.
    """

    def setUp(self):
        self.organizer = User.objects.create_user('organizer', password='pw12345678', is_staff=True)
        self.participant = User.objects.create_user('participant', password='pw12345678')
        self.judge_user = User.objects.create_user('judge', password='pw12345678')
        self.contest = make_contest()

        self.team = Team.objects.create(contest=self.contest, name='팀 A')
        self.team.participants.create(user=self.participant)
        self.submission = Submission.objects.create(team=self.team, title='제출물')
        self.judge = Judge.objects.create(contest=self.contest, user=self.judge_user)
        Score.objects.create(
            submission=self.submission, judge=self.judge, round='preliminary', value=Decimal('80'),
        )
        Award.objects.create(contest=self.contest, rank=1, title='대상')

    def test_organizer_delete_cascades_to_every_child(self):
        self.client.force_authenticate(self.organizer)
        res = self.client.delete(f'/api/contests/{self.contest.slug}/')

        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Contest.objects.filter(slug=self.contest.slug).exists())
        # 팀·참가자·제출물·심사위원·점수·시상이 남으면 다음 대회 집계에 섞여 들어간다.
        self.assertFalse(Team.objects.exists())
        self.assertFalse(Participant.objects.exists())
        self.assertFalse(Submission.objects.exists())
        self.assertFalse(Judge.objects.exists())
        self.assertFalse(Score.objects.exists())
        self.assertFalse(Award.objects.exists())
        # 사용자 계정 자체는 대회에 딸린 데이터가 아니므로 살아남아야 한다.
        self.assertTrue(User.objects.filter(username='participant').exists())

    def test_participant_cannot_delete_contest(self):
        self.client.force_authenticate(self.participant)
        res = self.client.delete(f'/api/contests/{self.contest.slug}/')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Contest.objects.filter(slug=self.contest.slug).exists())

    def test_judge_cannot_delete_contest(self):
        self.client.force_authenticate(self.judge_user)
        res = self.client.delete(f'/api/contests/{self.contest.slug}/')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Contest.objects.filter(slug=self.contest.slug).exists())

    def test_anonymous_cannot_delete_contest(self):
        res = self.client.delete(f'/api/contests/{self.contest.slug}/')
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertTrue(Contest.objects.filter(slug=self.contest.slug).exists())

    def test_delete_leaves_other_contests_untouched(self):
        other = make_contest(slug='hack-2027', name='2027 해커톤')
        other_team = Team.objects.create(contest=other, name='다른 팀')

        self.client.force_authenticate(self.organizer)
        res = self.client.delete(f'/api/contests/{self.contest.slug}/')

        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertTrue(Contest.objects.filter(slug=other.slug).exists())
        self.assertTrue(Team.objects.filter(pk=other_team.pk).exists())


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

    def test_organizer_can_move_status_backward(self):
        """실수로 한 단계 넘겼거나 모집을 다시 열어야 할 때 되돌릴 수 있어야 한다."""
        self.client.force_authenticate(self.organizer)
        self.contest.status = Contest.Status.ONGOING
        self.contest.save(update_fields=['status'])

        res = self.client.patch(f'/api/contests/{self.contest.slug}/', {'status': 'recruiting'})
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.contest.refresh_from_db()
        self.assertEqual(self.contest.status, 'recruiting')

    def test_organizer_can_skip_statuses_in_both_directions(self):
        self.client.force_authenticate(self.organizer)
        for target in ['closed', 'recruiting', 'judging', 'ongoing']:
            res = self.client.patch(f'/api/contests/{self.contest.slug}/', {'status': target})
            self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
            self.contest.refresh_from_db()
            self.assertEqual(self.contest.status, target)

    def test_reverting_status_keeps_teams_and_scores(self):
        """상태를 되돌려도 데이터는 남는다 — 상태 필드만 바뀐다."""
        team = Team.objects.create(contest=self.contest, name='팀 A')
        submission = Submission.objects.create(team=team, title='제출물')
        judge = Judge.objects.create(contest=self.contest, user=self.organizer)
        Score.objects.create(
            submission=submission, judge=judge, round='preliminary', value=Decimal('7'),
        )

        self.client.force_authenticate(self.organizer)
        self.contest.status = Contest.Status.JUDGING
        self.contest.save(update_fields=['status'])
        res = self.client.patch(f'/api/contests/{self.contest.slug}/', {'status': 'recruiting'})

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertTrue(Team.objects.filter(pk=team.pk).exists())
        self.assertTrue(Submission.objects.filter(pk=submission.pk).exists())
        self.assertEqual(Score.objects.count(), 1)

    def test_reopening_recruiting_lets_teams_form_again(self):
        """되돌리기의 목적 자체 — 진행중에 막혔던 팀 생성이 모집중으로 돌아오면 다시 열린다."""
        self.contest.status = Contest.Status.ONGOING
        self.contest.save(update_fields=['status'])

        self.client.force_authenticate(self.participant)
        res = self.client.post('/api/teams/', {'contest': self.contest.slug, 'name': '늦은 팀'})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.organizer)
        self.client.patch(f'/api/contests/{self.contest.slug}/', {'status': 'recruiting'})

        self.client.force_authenticate(self.participant)
        res = self.client.post('/api/teams/', {'contest': self.contest.slug, 'name': '늦은 팀'})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

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

    def test_team_can_be_created_while_recruiting(self):
        self.client.force_authenticate(self.participant)
        res = self.client.post('/api/teams/', {'contest': self.contest.slug, 'name': '팀 B'})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_team_cannot_be_created_once_ongoing(self):
        # 대회가 시작된 뒤 팀이 새로 생기면 발표 순서·심사 배정이 이미 정해진 뒤에 인원이 바뀐다.
        self.set_status(Contest.Status.ONGOING)
        self.client.force_authenticate(self.participant)
        res = self.client.post('/api/teams/', {'contest': self.contest.slug, 'name': '팀 B'})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn('진행중', res.data['detail'])

    def test_cannot_join_team_once_ongoing(self):
        self.set_status(Contest.Status.ONGOING)
        latecomer = User.objects.create_user('latecomer2', password='pw12345678')
        self.client.force_authenticate(latecomer)
        res = self.client.post(f'/api/teams/{self.team.id}/join/')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(self.team.participants.filter(user=latecomer).exists())

    def test_submission_still_editable_while_ongoing(self):
        # 팀 모집만 좁혔고 제출물은 그대로다 — 진행중이 곧 개발 시간이다.
        submission = Submission.objects.create(team=self.team, title='초안')
        self.set_status(Contest.Status.ONGOING)
        self.client.force_authenticate(self.participant)
        res = self.client.patch(f'/api/submissions/{submission.id}/', {'title': '진행중 수정'})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        submission.refresh_from_db()
        self.assertEqual(submission.title, '진행중 수정')

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


class ScoreboardQueryCountTests(ApiTestCase):
    """스코어보드는 5초마다 접속자 수만큼 불린다 — 캐시 적중 시 쿼리가 늘어나면 안 된다."""

    def setUp(self):
        self.judge_user = User.objects.create_user('judge1', password='pw12345678')
        self.contest = make_contest()
        team = Team.objects.create(contest=self.contest, name='팀 A')
        Submission.objects.create(team=team, title='제출물')
        Judge.objects.create(contest=self.contest, user=self.judge_user)

    def test_cache_hit_costs_one_query_for_a_judge(self):
        """심사위원 여부는 get_object() 의 annotate 로 이미 나와 있다 — EXISTS 를 또 던지지 않는다.

        캐시가 채워진 뒤 남는 쿼리는 대회 조회 하나뿐이다(테스트는 force_authenticate 라
        사용자 로드 쿼리가 없다).
        """
        self.client.force_authenticate(self.judge_user)
        self.client.get(f'/api/contests/{self.contest.slug}/scoreboard/')  # 캐시 채우기

        with self.assertNumQueries(1):
            res = self.client.get(f'/api/contests/{self.contest.slug}/scoreboard/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_judge_still_sees_final_round_through_the_annotation(self):
        """쿼리를 줄이면서 심사위원 권한이 조용히 떨어지지 않았는지 확인한다."""
        self.client.force_authenticate(self.judge_user)
        res = self.client.get(f'/api/contests/{self.contest.slug}/scoreboard/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(any(entry['round'] == 'final' for entry in res.data))

    def test_anonymous_viewer_still_gets_public_board_only(self):
        res = self.client.get(f'/api/contests/{self.contest.slug}/scoreboard/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(any(entry['round'] == 'final' for entry in res.data))


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
    """발표는 시계에 맞춘 예정표가 아니라 운영자가 버튼을 눌러 시작하는 이벤트다."""

    def setUp(self):
        self.organizer = User.objects.create_user('organizer', password='pw12345678', is_staff=True)
        self.participant = User.objects.create_user('participant', password='pw12345678')
        self.contest = make_contest()
        self.team_no_submission = Team.objects.create(contest=self.contest, name='팀 나중')
        self.team_with_submission = Team.objects.create(contest=self.contest, name='팀 먼저')
        self.team_with_submission.participants.create(user=self.participant)
        Submission.objects.create(team=self.team_with_submission, title='제출물')

    def test_organizer_can_assign_presentation_order(self):
        self.client.force_authenticate(self.organizer)
        res = self.client.post(f'/api/contests/{self.contest.slug}/assign_presentation_order/')
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)

        self.team_with_submission.refresh_from_db()
        self.team_no_submission.refresh_from_db()
        # 제출한 팀이 먼저, 미제출 팀은 뒤로. 시작 시각은 여기서 정하지 않는다.
        self.assertEqual(self.team_with_submission.presentation_order, 1)
        self.assertEqual(self.team_no_submission.presentation_order, 2)
        self.assertIsNone(self.team_with_submission.presentation_started_at)

    def test_non_organizer_cannot_assign_presentation_order(self):
        self.client.force_authenticate(self.participant)
        res = self.client.post(f'/api/contests/{self.contest.slug}/assign_presentation_order/')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.team_with_submission.refresh_from_db()
        self.assertIsNone(self.team_with_submission.presentation_order)

    def test_organizer_can_reorder_teams_freely(self):
        self.client.force_authenticate(self.organizer)
        self.client.post(f'/api/contests/{self.contest.slug}/assign_presentation_order/')

        res = self.client.patch(
            f'/api/teams/{self.team_no_submission.id}/', {'presentation_order': 1}
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.team_no_submission.refresh_from_db()
        self.assertEqual(self.team_no_submission.presentation_order, 1)

    def test_participant_cannot_change_own_presentation_order(self):
        """팀 수정 권한은 참가자에게도 있으므로 발표 순서만 따로 잠근다."""
        self.team_with_submission.presentation_order = 2
        self.team_with_submission.save(update_fields=['presentation_order'])

        self.client.force_authenticate(self.participant)
        res = self.client.patch(
            f'/api/teams/{self.team_with_submission.id}/', {'presentation_order': 1}
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.team_with_submission.refresh_from_db()
        self.assertEqual(self.team_with_submission.presentation_order, 2)

    def test_organizer_can_set_per_team_minutes_within_range(self):
        self.client.force_authenticate(self.organizer)
        res = self.client.patch(
            f'/api/teams/{self.team_with_submission.id}/', {'presentation_minutes': 7}
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertEqual(res.data['effective_presentation_minutes'], 7)

    def test_per_team_minutes_over_thirty_is_rejected(self):
        self.client.force_authenticate(self.organizer)
        res = self.client.patch(
            f'/api/teams/{self.team_with_submission.id}/', {'presentation_minutes': 31}
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_team_without_own_minutes_falls_back_to_contest_default(self):
        self.client.force_authenticate(self.organizer)
        res = self.client.get(f'/api/teams/?contest={self.contest.slug}')
        entry = next(t for t in res.data if t['id'] == self.team_with_submission.id)
        self.assertIsNone(entry['presentation_minutes'])
        self.assertEqual(entry['effective_presentation_minutes'], self.contest.presentation_minutes)

    def test_starting_a_presentation_records_the_actual_time(self):
        self.client.force_authenticate(self.organizer)
        res = self.client.post(f'/api/teams/{self.team_with_submission.id}/start_presentation/')
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.team_with_submission.refresh_from_db()
        self.assertIsNotNone(self.team_with_submission.presentation_started_at)
        self.assertIsNone(self.team_with_submission.presentation_ended_at)
        # 종료 예정 시각은 시작 시각 + 그 팀의 발표 시간이다.
        self.assertIsNotNone(res.data['presentation_due_at'])

    def test_starting_a_team_ends_the_one_still_running(self):
        """한 대회에서 두 팀이 동시에 발표할 수는 없다."""
        self.client.force_authenticate(self.organizer)
        self.client.post(f'/api/teams/{self.team_with_submission.id}/start_presentation/')
        self.client.post(f'/api/teams/{self.team_no_submission.id}/start_presentation/')

        self.team_with_submission.refresh_from_db()
        self.team_no_submission.refresh_from_db()
        self.assertIsNotNone(self.team_with_submission.presentation_ended_at)
        self.assertIsNone(self.team_no_submission.presentation_ended_at)

    def test_ended_presentation_stops_the_timer(self):
        """끝난 팀은 due_at 이 null 이라 프론트 타이머가 돌지 않는다."""
        self.client.force_authenticate(self.organizer)
        self.client.post(f'/api/teams/{self.team_with_submission.id}/start_presentation/')
        res = self.client.post(f'/api/teams/{self.team_with_submission.id}/end_presentation/')

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertIsNone(res.data['presentation_due_at'])
        self.assertIsNotNone(res.data['presentation_ended_at'])

    def test_no_team_running_means_no_timer_anywhere(self):
        """팀 교체·쉬는 시간 — 아무도 시작하지 않았으면 모든 팀의 due_at 이 null 이다."""
        self.client.force_authenticate(self.organizer)
        self.client.post(f'/api/contests/{self.contest.slug}/assign_presentation_order/')
        res = self.client.get(f'/api/teams/?contest={self.contest.slug}')
        self.assertTrue(all(t['presentation_due_at'] is None for t in res.data))

    def test_ending_a_presentation_that_never_started_is_rejected(self):
        self.client.force_authenticate(self.organizer)
        res = self.client.post(f'/api/teams/{self.team_with_submission.id}/end_presentation/')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_organizer_can_reset_a_presentation_started_by_mistake(self):
        self.client.force_authenticate(self.organizer)
        self.client.post(f'/api/teams/{self.team_with_submission.id}/start_presentation/')
        res = self.client.post(f'/api/teams/{self.team_with_submission.id}/reset_presentation/')

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertIsNone(res.data['presentation_started_at'])
        self.assertIsNone(res.data['presentation_due_at'])

    def test_participant_cannot_start_a_presentation(self):
        self.client.force_authenticate(self.participant)
        res = self.client.post(f'/api/teams/{self.team_with_submission.id}/start_presentation/')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.team_with_submission.refresh_from_db()
        self.assertIsNone(self.team_with_submission.presentation_started_at)


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


class ScoreboardCorsTests(ApiTestCase):
    """조건부 폴링은 CORS 양쪽이 다 열려 있어야 브라우저에서 동작한다.

    이 두 헤더가 빠지면 서버 테스트는 전부 통과하는데 브라우저에서만 조용히 멈춘다
    (서버 로그에는 OPTIONS 만 남는다). 그래서 설정 자체를 테스트로 고정한다.
    """

    ORIGIN = 'https://hackman-sju.vercel.app'

    def setUp(self):
        self.contest = make_contest()
        self.url = f'/api/contests/{self.contest.slug}/scoreboard/'

    def test_preflight_allows_if_none_match(self):
        with self.settings(CORS_ALLOWED_ORIGINS=[self.ORIGIN]):
            res = self.client.options(
                self.url,
                HTTP_ORIGIN=self.ORIGIN,
                HTTP_ACCESS_CONTROL_REQUEST_METHOD='GET',
                HTTP_ACCESS_CONTROL_REQUEST_HEADERS='if-none-match',
            )
        allowed = res.headers.get('access-control-allow-headers', '').lower()
        self.assertIn('if-none-match', allowed)

    def test_etag_is_exposed_to_javascript(self):
        with self.settings(CORS_ALLOWED_ORIGINS=[self.ORIGIN]):
            res = self.client.get(self.url, HTTP_ORIGIN=self.ORIGIN)
        exposed = res.headers.get('access-control-expose-headers', '').lower()
        self.assertIn('etag', exposed)

    def test_304_still_carries_the_allow_origin_header(self):
        # CORS 헤더가 없는 304 는 브라우저가 네트워크 오류로 처리해 폴링이 끊긴다.
        with self.settings(CORS_ALLOWED_ORIGINS=[self.ORIGIN]):
            first = self.client.get(self.url, HTTP_ORIGIN=self.ORIGIN)
            second = self.client.get(
                self.url, HTTP_ORIGIN=self.ORIGIN, HTTP_IF_NONE_MATCH=first['ETag'],
            )
        self.assertEqual(second.status_code, status.HTTP_304_NOT_MODIFIED)
        self.assertEqual(second.headers.get('access-control-allow-origin'), self.ORIGIN)


class ProfileApiTests(ApiTestCase):
    """팀빌딩 프로필. 원문과 추출 결과를 둘 다 보관하고, 본인만 읽고 쓴다."""

    def setUp(self):
        self.alice = User.objects.create_user('alice', password='pw12345678')
        self.bob = User.objects.create_user('bob', password='pw12345678')

    def test_profile_is_created_on_first_read(self):
        """참가자가 먼저 생성할 일이 없게 한다."""
        self.client.force_authenticate(self.alice)
        res = self.client.get('/api/profile/')
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertEqual(res.data['username'], 'alice')
        self.assertEqual(res.data['extraction_status'], 'empty')

    def test_anonymous_cannot_read_profile(self):
        res = self.client.get('/api/profile/')
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_participant_can_edit_extracted_tags(self):
        """모델이 뽑은 것을 사실로 굳히지 않는다 — 참가자가 고칠 수 있어야 한다."""
        self.client.force_authenticate(self.alice)
        res = self.client.patch('/api/profile/', {
            'skills': ['react', 'python'], 'roles': ['frontend'], 'level': 'intermediate',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertEqual(res.data['skills'], ['react', 'python'])
        self.assertEqual(res.data['level'], 'intermediate')

    def test_changing_intro_marks_extraction_stale(self):
        """원문이 바뀌면 기존 추출 결과는 그 원문에서 나온 것이 아니게 된다."""
        Profile.objects.create(
            user=self.alice, intro='예전 소개', skills=['react'],
            extraction_status=Profile.ExtractionStatus.DONE,
        )
        self.client.force_authenticate(self.alice)
        res = self.client.patch('/api/profile/', {'intro': '새로 쓴 소개'}, format='json')

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertEqual(res.data['extraction_status'], 'pending')
        # 지우지는 않는다 — 재추출 전까지 옛 태그라도 있는 편이 추천에 낫다.
        self.assertEqual(res.data['skills'], ['react'])

    def test_profile_endpoint_only_returns_own_profile(self):
        Profile.objects.create(user=self.bob, intro='밥의 소개', skills=['go'])
        self.client.force_authenticate(self.alice)
        res = self.client.get('/api/profile/')
        self.assertEqual(res.data['username'], 'alice')
        self.assertEqual(res.data['skills'], [])

    def test_llm_models_lists_only_configured_providers(self):
        self.client.force_authenticate(self.alice)
        with self.settings(ANTHROPIC_API_KEY='k', OPENAI_API_KEY='', GOOGLE_API_KEY=''):
            res = self.client.get('/api/llm/models/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual([p['provider'] for p in res.data['providers']], ['anthropic'])

    def test_llm_models_is_empty_without_keys(self):
        """키가 하나도 없으면 LLM 기능만 꺼지고 나머지는 동작해야 한다."""
        self.client.force_authenticate(self.alice)
        with self.settings(ANTHROPIC_API_KEY='', OPENAI_API_KEY='', GOOGLE_API_KEY=''):
            res = self.client.get('/api/llm/models/')
        self.assertEqual(res.data['providers'], [])

    def test_extract_without_any_key_fails_softly(self):
        """추출 실패가 팀빌딩을 막으면 안 된다 — 400 이 아니라 실패 상태를 돌려준다."""
        Profile.objects.create(user=self.alice, intro='웹 프론트 좀 했습니다')
        self.client.force_authenticate(self.alice)
        with self.settings(ANTHROPIC_API_KEY='', OPENAI_API_KEY='', GOOGLE_API_KEY=''):
            res = self.client.post('/api/profile/extract/')
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertEqual(res.data['extraction_status'], 'failed')
        self.assertTrue(res.data['extraction_error'])

    def test_extract_with_empty_intro_does_not_call_the_model(self):
        self.client.force_authenticate(self.alice)
        with mock.patch('contests.profile_extract.complete') as called:
            res = self.client.post('/api/profile/extract/')
        called.assert_not_called()
        self.assertEqual(res.data['extraction_status'], 'empty')

    def test_extract_fills_tags_from_free_text(self):
        Profile.objects.create(user=self.alice, intro='웹 프론트 좀 했고 파이썬도 조금 압니다')
        self.client.force_authenticate(self.alice)

        fake = mock.Mock(
            text='```json\n{"skills":["react","python"],"roles":["frontend"],'
                 '"interests":["교육"],"level":"beginner"}\n```',
            model='claude-opus-5',
        )
        with mock.patch('contests.profile_extract.complete', return_value=fake):
            res = self.client.post('/api/profile/extract/')

        self.assertEqual(res.data['extraction_status'], 'done')
        self.assertEqual(res.data['skills'], ['react', 'python'])
        self.assertEqual(res.data['roles'], ['frontend'])
        self.assertEqual(res.data['level'], 'beginner')

    def test_extract_drops_roles_the_model_invented(self):
        """모델이 목록 밖의 역할을 지어내는 일이 있어 서버에서 한 번 더 거른다."""
        Profile.objects.create(user=self.alice, intro='뭐든 합니다')
        self.client.force_authenticate(self.alice)

        fake = mock.Mock(
            text='{"skills":[],"roles":["frontend","우주비행사"],"interests":[],"level":"wizard"}',
            model='m',
        )
        with mock.patch('contests.profile_extract.complete', return_value=fake):
            res = self.client.post('/api/profile/extract/')

        self.assertEqual(res.data['roles'], ['frontend'])
        self.assertEqual(res.data['level'], '')  # 알 수 없는 값은 비운다


class TeamMatchingTests(ApiTestCase):
    """추천 순위는 전부 규칙 기반 — LLM 키 없이 동작하고 결과가 결정적이어야 한다."""

    def setUp(self):
        self.contest = make_contest()
        self.solo = User.objects.create_user('solo', password='pw12345678')
        Profile.objects.create(
            user=self.solo, skills=['react'], roles=['frontend'], interests=['교육'],
        )

    def _team_with(self, name, **profile_kwargs):
        team = Team.objects.create(contest=self.contest, name=name)
        member = User.objects.create_user(f'member-{name}', password='pw12345678')
        Profile.objects.create(user=member, **profile_kwargs)
        team.participants.create(user=member)
        return team

    def test_team_missing_my_role_ranks_above_one_that_has_it(self):
        """빈 역할을 채우는 것이 팀 구성에서 가장 값어치가 크다."""
        needs_frontend = self._team_with('백엔드뿐', roles=['backend'], skills=['django'])
        already_frontend = self._team_with('프론트있음', roles=['frontend'], skills=['react'])

        self.client.force_authenticate(self.solo)
        res = self.client.get(f'/api/contests/{self.contest.slug}/recommended_teams/')

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        order = [t['team_name'] for t in res.data['teams']]
        self.assertLess(order.index(needs_frontend.name), order.index(already_frontend.name))

    def test_reason_explains_the_ranking(self):
        """왜 그 순서인지 설명할 수 있어야 한다 — 운영자가 답할 수 있어야 하기 때문."""
        self._team_with('백엔드뿐', roles=['backend'])
        self.client.force_authenticate(self.solo)
        res = self.client.get(f'/api/contests/{self.contest.slug}/recommended_teams/')
        self.assertTrue(any(t['reasons'] for t in res.data['teams']))

    def test_user_already_on_a_team_gets_no_recommendations(self):
        team = Team.objects.create(contest=self.contest, name='내 팀')
        team.participants.create(user=self.solo)

        self.client.force_authenticate(self.solo)
        res = self.client.get(f'/api/contests/{self.contest.slug}/recommended_teams/')
        self.assertEqual(res.data['teams'], [])

    def test_full_teams_are_not_recommended(self):
        team = Team.objects.create(contest=self.contest, name='꽉 찬 팀')
        for i in range(4):
            u = User.objects.create_user(f'full{i}', password='pw12345678')
            Profile.objects.create(user=u)
            team.participants.create(user=u)

        self.client.force_authenticate(self.solo)
        res = self.client.get(f'/api/contests/{self.contest.slug}/recommended_teams/')
        self.assertEqual([t['team_name'] for t in res.data['teams']], [])

    def test_recommendations_work_without_any_llm_key(self):
        self._team_with('아무 팀', roles=['backend'])
        self.client.force_authenticate(self.solo)
        with self.settings(ANTHROPIC_API_KEY='', OPENAI_API_KEY='', GOOGLE_API_KEY=''):
            res = self.client.get(f'/api/contests/{self.contest.slug}/recommended_teams/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data['teams'])

    def test_candidates_exclude_people_who_already_have_a_team(self):
        team = self._team_with('구인 팀', roles=['backend'])
        taken = User.objects.create_user('taken', password='pw12345678')
        Profile.objects.create(user=taken, roles=['frontend'])
        Team.objects.create(contest=self.contest, name='다른 팀').participants.create(user=taken)

        self.client.force_authenticate(team.participants.first().user)
        res = self.client.get(f'/api/teams/{team.id}/candidates/')

        names = [c['username'] for c in res.data['candidates']]
        self.assertIn('solo', names)
        self.assertNotIn('taken', names)

    def test_outsider_cannot_see_team_candidates(self):
        team = self._team_with('남의 팀', roles=['backend'])
        self.client.force_authenticate(self.solo)
        res = self.client.get(f'/api/teams/{team.id}/candidates/')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_organizer_can_see_team_candidates(self):
        team = self._team_with('어떤 팀', roles=['backend'])
        organizer = User.objects.create_user('org', password='pw12345678', is_staff=True)
        self.client.force_authenticate(organizer)
        res = self.client.get(f'/api/teams/{team.id}/candidates/')
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)

    def test_candidates_respect_looking_for_team_flag(self):
        team = self._team_with('구인 팀', roles=['backend'])
        Profile.objects.filter(user=self.solo).update(looking_for_team=False)

        self.client.force_authenticate(team.participants.first().user)
        res = self.client.get(f'/api/teams/{team.id}/candidates/')
        self.assertNotIn('solo', [c['username'] for c in res.data['candidates']])
