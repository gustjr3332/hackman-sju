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
    # skills 는 `TechStack.slug` 목록이다 — 자유 문자열이면 react/리액트/React.js 가 다른
    # 스택으로 세어져 매칭이 조용히 망가진다(matching.py 의 `_normalize()` 는 대소문자·공백만
    # 처리한다).
    skills = models.JSONField(default=list, blank=True)
    # 정규 목록에 없어서 매핑하지 못한 스택. **버리지 않는다** — 버리면 참가자가 실제로 쓴
    # 기술이 사라지고, 운영자가 목록에 무엇을 추가해야 하는지도 알 수 없게 된다. 자기소개
    # 자동 정리에서만 생기고 참가자가 직접 타이핑하는 경로는 두지 않는다.
    other_skills = models.JSONField(default=list, blank=True)
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


class TechStack(models.Model):
    """정규 기술 스택 목록. 프로필의 `skills` 가 참조하는 정본이다.

    **코드 상수가 아니라 DB 테이블인 이유는 운영자가 대회 중에 목록을 고칠 수 있어야 하기
    때문이다.** 상수라면 목록에 없는 스택이 대회 당일 나왔을 때 재배포 전에는 손을 쓸 수 없고,
    Render 무료 인스턴스라 재배포에는 콜드 스타트(30~50초)까지 따라붙는다. 관리 화면은 따로
    만들지 않고 Django admin 을 쓴다 — 운영자가 목록을 고치는 빈도에 비해 전용 화면은 과하다.

    삭제 대신 `is_active` 를 내린다. 이미 프로필이 참조 중인 스택을 지우면 과거 프로필의
    태그가 말없이 사라진다. 비활성 스택은 새로 고를 수 없고 기존 참조는 남는다.
    """

    class Category(models.TextChoices):
        LANGUAGE = 'language', '언어'
        FRAMEWORK = 'framework', '프레임워크·라이브러리'
        TOOL = 'tool', '도구·인프라'

    # 프로필에 저장되는 값이자 API 가 주고받는 키. GitHub 언어 목록의 표기를 소문자화한 것.
    slug = models.SlugField(primary_key=True, max_length=60)
    name = models.CharField(max_length=60)
    category = models.CharField(
        max_length=20, choices=Category.choices, default=Category.LANGUAGE
    )
    # `react` / `React.js` / `리액트` 가 같은 것을 가리킨다는 사실을 적어 두는 자리. 자동
    # 정리가 뽑은 문자열을 여기까지 훑어 정규 태그로 바꾼다.
    aliases = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['category', 'name']

    def __str__(self):
        return self.name


class SubmissionReview(models.Model):
    """심사 보조: 제출 저장소를 LLM 이 미리 읽고 정리한 결과.

    **점수를 제안하지 않는다.** 제안 점수를 띄우면 심사위원이 거기에 닻을 내려(anchoring)
    결국 모델이 채점하는 것과 같아진다. 출력은 "무엇이 있는지"까지고 판단은 사람이 한다.
    대신 근거로 삼은 파일 경로를 반드시 남겨 심사위원이 직접 열어 확인할 수 있게 한다.

    OneToOne 이 아니라 ForeignKey 다 — 같은 제출물을 여러 모델로 돌려 나란히 놓고 비교하는
    것이 이 기능의 목적 중 하나이기 때문이다(어느 모델이 쓸 만한지는 실제 제출물로 재봐야
    안다). `(submission, provider, model)` 이 유일하고, 같은 조합을 다시 돌리면 덮어쓴다.
    """

    class Status(models.TextChoices):
        PENDING = 'pending', '분석 중'
        DONE = 'done', '분석 완료'
        FAILED = 'failed', '분석 실패'

    submission = models.ForeignKey(
        Submission, related_name='reviews', on_delete=models.CASCADE
    )
    provider = models.CharField(max_length=20)
    model = models.CharField(max_length=80)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    # 이 프로젝트가 실제로 하는 일 (README 주장이 아니라 코드 기준).
    summary = models.TextField(blank=True)
    # 항목 목록. 각 항목은 {kind, title, detail, paths} — kind 는 implemented/shell/note.
    findings = models.JSONField(default=list, blank=True)
    # 근거로 삼은 파일 경로. 심사위원이 직접 열어 확인하는 통로라 비면 안 된다.
    cited_paths = models.JSONField(default=list, blank=True)
    stack = models.JSONField(default=list, blank=True)
    # 저장소가 커서 잘라 넣었는지 등, 분석 자체의 한계. 조용히 자르지 않는다.
    truncated = models.BooleanField(default=False)
    files_read = models.PositiveIntegerField(default=0)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    # 분석이 읽은 시점의 제출물 수정 시각. 제출물이 그 뒤에 바뀌었으면 이 분석은 낡은 것이다.
    # 행을 지우거나 상태를 되돌리지 않고 시각 비교로 판정한다 — 낡았어도 없는 것보다는 낫고,
    # 무엇을 다시 돌려야 하는지는 화면에서 보이면 된다.
    submission_seen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['provider', 'model']
        unique_together = ('submission', 'provider', 'model')

    def __str__(self):
        return f'{self.submission} analyzed by {self.provider}/{self.model}'

    @property
    def is_stale(self):
        """분석 이후 제출물이 바뀌었는지. 바뀌었으면 재분석 대상이다."""
        if self.submission_seen_at is None:
            return False
        return self.submission.submitted_at > self.submission_seen_at
