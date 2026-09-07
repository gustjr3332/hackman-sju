from django.urls import path
from rest_framework.routers import DefaultRouter

from .github import GithubProxyView
from .views import (
    AwardViewSet,
    ContestViewSet,
    JudgeViewSet,
    ScoreViewSet,
    SubmissionViewSet,
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
]
