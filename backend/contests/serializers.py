from datetime import timedelta

from django.contrib.auth import get_user_model
from rest_framework import serializers
from rest_framework.validators import UniqueTogetherValidator

from .models import Award, Contest, Judge, Participant, Profile, Score, Submission, Team

User = get_user_model()


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'password']

    def create(self, validated_data):
        return User.objects.create_user(
            username=validated_data['username'],
            email=validated_data.get('email', ''),
            password=validated_data['password'],
        )


class ParticipantSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)

    class Meta:
        model = Participant
        fields = ['id', 'team', 'user', 'username', 'joined_at']
        read_only_fields = ['joined_at']
        extra_kwargs = {'user': {'write_only': True}}


class SubmissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Submission
        fields = ['id', 'team', 'title', 'description', 'link_url', 'repo_url', 'submitted_at']
        read_only_fields = ['submitted_at']


class TeamSerializer(serializers.ModelSerializer):
    participants = ParticipantSerializer(many=True, read_only=True)
    submission = SubmissionSerializer(read_only=True)
    # 발표 시작 시각은 운영자가 "발표 시작"을 누른 실제 시각이다(예정표가 아니다). 종료 예정
    # 시각만 시작 시각 + 이 팀의 발표 시간으로 계산해 프론트 타이머가 쓸 수 있게 내려준다.
    # 아직 시작하지 않았거나 이미 끝난 팀은 null 이라 타이머가 돌지 않는다.
    presentation_due_at = serializers.SerializerMethodField()
    effective_presentation_minutes = serializers.IntegerField(read_only=True)

    class Meta:
        model = Team
        fields = [
            'id', 'contest', 'name', 'created_at', 'participants', 'submission',
            'presentation_order', 'presentation_minutes', 'effective_presentation_minutes',
            'presentation_started_at', 'presentation_ended_at', 'presentation_due_at',
        ]
        read_only_fields = ['created_at', 'presentation_started_at', 'presentation_ended_at']
        validators = [
            UniqueTogetherValidator(
                queryset=Team.objects.all(),
                fields=['contest', 'name'],
                message='이미 이 대회에 같은 이름의 팀이 있습니다.',
            ),
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 발표 순서·발표 시간은 운영자만 정한다. 팀 수정 권한(IsTeamMemberOrReadOnly)은 팀
        # 참가자에게도 있으므로, 여기서 막지 않으면 참가자가 자기 팀 순서를 앞당길 수 있다.
        user = getattr(self.context.get('request'), 'user', None)
        if not (user and user.is_authenticated and user.is_staff):
            self.fields['presentation_order'].read_only = True
            self.fields['presentation_minutes'].read_only = True

    def get_presentation_due_at(self, obj):
        if obj.presentation_started_at is None or obj.presentation_ended_at is not None:
            return None
        due = obj.presentation_started_at + timedelta(minutes=obj.effective_presentation_minutes)
        return serializers.DateTimeField().to_representation(due)


class ContestSerializer(serializers.ModelSerializer):
    # 목록/상세 조회는 ContestViewSet.get_queryset 의 annotate 값을 그대로 쓰므로 대회 수와
    # 무관하게 쿼리 수가 일정하다. annotate 가 없는 인스턴스(생성/수정 직후)만 직접 계산한다.
    team_count = serializers.SerializerMethodField()
    # 요청한 사용자가 이 대회의 심사위원인지. 프론트가 심사위원 목록을 받아 아이디를
    # 비교하는 대신 서버 판단을 그대로 쓰고, 폴링으로 배정 변경이 자동 반영된다.
    is_judge = serializers.SerializerMethodField()

    # 상태 전이에 순서 제약은 없다. 운영자가 대회의 시간선을 자유롭게 오갈 수 있어야 한다 —
    # 실수로 한 단계 넘겼거나(진행중을 너무 일찍 눌렀다), 모집을 다시 열거나(모집중으로 되돌림),
    # 시상식을 다시 하려면(종료 → 심사중) 되돌리는 길이 있어야 한다. 되돌려도 팀·제출물·점수·
    # 시상은 그대로 남는다(상태 필드만 바뀐다). 누가 바꿀 수 있는지는
    # ContestViewSet.permission_classes = [IsOrganizerOrReadOnly] 가 지킨다 — 운영자만이다.
    # 유효하지 않은 값은 여기가 아니라 model choices 가 걸러 400 을 낸다.

    class Meta:
        model = Contest
        fields = [
            'slug', 'name', 'description', 'status',
            'start_at', 'end_at', 'created_at', 'updated_at', 'team_count', 'is_judge',
            'presentation_minutes',
        ]
        read_only_fields = ['created_at', 'updated_at']

    def validate(self, attrs):
        start_at = attrs.get('start_at', getattr(self.instance, 'start_at', None))
        end_at = attrs.get('end_at', getattr(self.instance, 'end_at', None))
        if start_at and end_at and end_at < start_at:
            raise serializers.ValidationError({'end_at': '종료 일시는 시작 일시보다 빨라서는 안 됩니다.'})

        return attrs

    def get_team_count(self, obj):
        count = getattr(obj, 'team_count', None)
        return count if count is not None else obj.teams.count()

    def get_is_judge(self, obj):
        value = getattr(obj, 'is_judge', None)
        if value is not None:
            return value
        user = getattr(self.context.get('request'), 'user', None)
        if user is None or not user.is_authenticated:
            return False
        return obj.judges.filter(user=user).exists()


class JudgeSerializer(serializers.ModelSerializer):
    # 읽기와 쓰기 모두 아이디 문자열 하나(`username`)로 통일한다.
    username = serializers.SlugRelatedField(
        source='user',
        slug_field='username',
        queryset=User.objects.all(),
        error_messages={'does_not_exist': '존재하지 않는 사용자입니다.'},
    )
    # 이 심사위원이 입력한 점수 수. 0 이 아니면 해제할 수 없다 (JudgeViewSet.perform_destroy).
    score_count = serializers.SerializerMethodField()

    class Meta:
        model = Judge
        fields = ['id', 'contest', 'username', 'score_count']
        validators = [
            UniqueTogetherValidator(
                queryset=Judge.objects.all(),
                fields=['contest', 'username'],
                message='이미 이 대회의 심사위원으로 배정된 사용자입니다.',
            ),
        ]

    def get_score_count(self, obj):
        count = getattr(obj, 'score_count', None)
        return count if count is not None else obj.scores.count()


class MeSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['username', 'is_staff']


class ScoreSerializer(serializers.ModelSerializer):
    # perform_create 가 submission.team.contest 까지 타고 올라가므로 한 번에 조인해 둔다.
    submission = serializers.PrimaryKeyRelatedField(
        queryset=Submission.objects.select_related('team__contest')
    )
    judge_username = serializers.CharField(source='judge.user.username', read_only=True)

    class Meta:
        model = Score
        fields = [
            'id', 'submission', 'judge', 'judge_username',
            'round', 'value', 'comment', 'created_at', 'updated_at',
        ]
        read_only_fields = ['judge', 'judge_username', 'created_at', 'updated_at']


class ScoreboardEntrySerializer(serializers.Serializer):
    team_id = serializers.IntegerField()
    team_name = serializers.CharField()
    submission_title = serializers.CharField(allow_null=True)
    round = serializers.CharField()
    average_score = serializers.DecimalField(max_digits=6, decimal_places=2, allow_null=True)
    vote_count = serializers.IntegerField()
    rank = serializers.IntegerField(allow_null=True)


class AwardSerializer(serializers.ModelSerializer):
    class Meta:
        model = Award
        fields = ['id', 'contest', 'rank', 'title']
        validators = [
            UniqueTogetherValidator(
                queryset=Award.objects.all(),
                fields=['contest', 'rank'],
                message='이미 이 등수에 배정된 상이 있습니다.',
            ),
        ]


class ProfileSerializer(serializers.ModelSerializer):
    """팀빌딩 프로필. 참가자는 자기 것만 읽고 쓴다.

    추출 결과(skills/roles/interests/level)도 쓰기 가능하다 — 모델이 뽑은 것을 사실로 굳히지
    않고 참가자가 고칠 수 있어야 하기 때문이다. 추출 상태는 서버가 정하므로 읽기 전용이다.
    """

    username = serializers.CharField(source='user.username', read_only=True)

    class Meta:
        model = Profile
        fields = [
            'username', 'intro', 'skills', 'interests', 'roles', 'level',
            'looking_for_team', 'extraction_status', 'extraction_error',
            'extracted_by', 'extracted_at', 'updated_at',
        ]
        read_only_fields = [
            'extraction_status', 'extraction_error', 'extracted_by',
            'extracted_at', 'updated_at',
        ]
