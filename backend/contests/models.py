from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Contest(models.Model):
    class Status(models.TextChoices):
        RECRUITING = 'recruiting', '모집중'
        ONGOING = 'ongoing', '진행중'
        JUDGING = 'judging', '심사중'
        CLOSED = 'closed', '종료'

    slug = models.SlugField(primary_key=True, max_length=80)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RECRUITING)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    # 팀이 따로 정하지 않았을 때 쓰는 기본 발표 시간(분). 실제 시작 시각은 저장하지 않는다 —
    # 운영자가 팀마다 "발표 시작"을 눌러야 시작되고, 그 시각이 Team 에 기록된다.
    presentation_minutes = models.PositiveIntegerField(
        default=10, validators=[MinValueValidator(1), MaxValueValidator(30)]
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-start_at']

    def __str__(self):
        return self.name


class Team(models.Model):
    contest = models.ForeignKey(Contest, related_name='teams', on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    # 발표 순서(1부터). assign_presentation_order 가 제출 시각순으로 한 번에 채우고, 그 뒤로는
    # 운영자가 자유롭게 재배치할 수 있다.
    presentation_order = models.PositiveIntegerField(null=True, blank=True)
    # 이 팀만의 발표 시간(분). null 이면 contest.presentation_minutes 를 따른다.
    presentation_minutes = models.PositiveIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(30)]
    )
    # 운영자가 "발표 시작"을 누른 실제 시각. 시계에 맞춘 예정표가 아니라 실제로 벌어진 일을
    # 기록한다 — 팀 교체·쉬는 시간에는 아무 팀도 시작 상태가 아니므로 타이머가 흐르지 않는다.
    presentation_started_at = models.DateTimeField(null=True, blank=True)
    presentation_ended_at = models.DateTimeField(null=True, blank=True)

    @property
    def effective_presentation_minutes(self):
        return self.presentation_minutes or self.contest.presentation_minutes

    class Meta:
        ordering = ['name']
        unique_together = ('contest', 'name')

    def __str__(self):
        return f'{self.contest_id}/{self.name}'


class Participant(models.Model):
    team = models.ForeignKey(Team, related_name='participants', on_delete=models.CASCADE)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name='participations', on_delete=models.CASCADE
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('team', 'user')

    def __str__(self):
        return f'{self.user} in {self.team}'


class Submission(models.Model):
    team = models.OneToOneField(Team, related_name='submission', on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    link_url = models.URLField(blank=True)
    repo_url = models.URLField(blank=True)
    submitted_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title


class Judge(models.Model):
    contest = models.ForeignKey(Contest, related_name='judges', on_delete=models.CASCADE)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name='judging_contests', on_delete=models.CASCADE
    )

    class Meta:
        unique_together = ('contest', 'user')

    def __str__(self):
        return f'{self.user} judging {self.contest}'


class Score(models.Model):
    class Round(models.TextChoices):
        PRELIMINARY = 'preliminary', '예선'
        FINAL = 'final', '결선'

    submission = models.ForeignKey(Submission, related_name='scores', on_delete=models.CASCADE)
    judge = models.ForeignKey(Judge, related_name='scores', on_delete=models.CASCADE)
    round = models.CharField(max_length=20, choices=Round.choices, default=Round.PRELIMINARY)
    value = models.DecimalField(
        max_digits=5, decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('submission', 'judge', 'round')

    def __str__(self):
        return f'{self.judge} -> {self.submission} ({self.round}): {self.value}'


class Award(models.Model):
    """시상식에서 rank 등수에 붙일 상 이름(대상/최우수상/창의상 등). rank 1이 최상위."""

    contest = models.ForeignKey(Contest, related_name='awards', on_delete=models.CASCADE)
    rank = models.PositiveIntegerField()
    title = models.CharField(max_length=50)

    class Meta:
        ordering = ['rank']
        unique_together = ('contest', 'rank')

    def __str__(self):
        return f'{self.contest_id} #{self.rank} {self.title}'


class Profile(models.Model):
    """팀빌딩용 참가자 프로필.

    참가자는 체크박스 스무 개를 채우지 않는다. 자유 서술 한 문단(`intro`)을 받아 LLM 으로
    구조화한 결과를 나머지 필드에 담는다. **원문과 추출 결과를 둘 다 보관한다** — 추출이
    틀렸을 때 참가자가 고칠 수 있어야 하고, 추출 프롬프트를 바꾸면 원문에서 다시 뽑을 수
    있어야 하기 때문이다.

    대회별이 아니라 계정에 하나만 둔다. 대회마다 다시 쓰게 하면 마찰이 커서 정작 프로필이
    비게 되고, 이 기능의 목표("혼자 온 사람이 팀을 찾는다")가 바로 무너진다.
    """

    class ExtractionStatus(models.TextChoices):
        EMPTY = 'empty', '추출 안 함'
        PENDING = 'pending', '추출 대기'
        DONE = 'done', '추출 완료'
        FAILED = 'failed', '추출 실패'

    class Level(models.TextChoices):
        BEGINNER = 'beginner', '입문'
        INTERMEDIATE = 'intermediate', '중급'
        ADVANCED = 'advanced', '숙련'

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, related_name='profile', on_delete=models.CASCADE
    )
    # 참가자가 직접 쓴 원문. 이것이 사실의 원본이고 나머지는 여기서 파생된다.
    intro = models.TextField(blank=True)
    # 개인 GitHub 주소. 태그만으로는 안 보이는 실제 결과물을 팀이 직접 확인하는 통로다.
    github_url = models.URLField(blank=True)
    # 추출 결과. 참가자가 화면에서 직접 고칠 수 있어야 하므로 읽기 전용이 아니다.
    skills = models.JSONField(default=list, blank=True)
    interests = models.JSONField(default=list, blank=True)
    roles = models.JSONField(default=list, blank=True)
    level = models.CharField(
        max_length=20, choices=Level.choices, blank=True, default=''
    )
    # 팀이 없는 사람을 추천 대상으로 올릴지. 이 기능이 겨냥하는 바로 그 사람들이다.
    looking_for_team = models.BooleanField(default=True)

    extraction_status = models.CharField(
        max_length=20, choices=ExtractionStatus.choices, default=ExtractionStatus.EMPTY
    )
    extraction_error = models.TextField(blank=True)
    # 어떤 제공사·모델이 뽑았는지. 나중에 프롬프트나 모델을 바꿨을 때 재추출 대상을 고른다.
    extracted_by = models.CharField(max_length=80, blank=True, default='')
    extracted_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'profile of {self.user}'
