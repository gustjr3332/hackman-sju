from django.urls import path
from rest_framework.routers import DefaultRouter

from .github import GithubProxyView
from .views import (
    AwardViewSet,
    ContestViewSet,
    JudgeViewSet,
    LlmModelsView,
    MyProfileView,
    ProfileExtractView,
    ScoreViewSet,
    SubmissionViewSet,
    TeamCandidateView,
    TeamRecommendationView,
    TeamViewSet,
)

router = DefaultRouter()
router.register('contests', ContestViewSet)
router.register('teams', TeamViewSet, basename='team')
router.register('submissions', SubmissionViewSet, basename='submission')
router.register('judges', JudgeViewSet, basename='judge')
router.register('scores', ScoreViewSet, basename='score')
router.register('awards', AwardViewSet, basename='award')

urlpatterns = router.urls + [
    # 심사 도구가 쓰는 GitHub 읽기 프록시 (contests/github.py 주석 참고).
    path(
        'github/<str:resource>/',
        GithubProxyView.as_view(),
        name='github-proxy',
    ),
    # 팀빌딩: 프로필은 계정에 하나, 추천은 대회별로 계산한다.
    path('profile/', MyProfileView.as_view(), name='my-profile'),
    path('profile/extract/', ProfileExtractView.as_view(), name='profile-extract'),
    path('llm/models/', LlmModelsView.as_view(), name='llm-models'),
    path(
        'contests/<slug:slug>/recommended_teams/',
        TeamRecommendationView.as_view(),
        name='recommended-teams',
    ),
    path('teams/<int:pk>/candidates/', TeamCandidateView.as_view(), name='team-candidates'),
]
