from django.contrib import admin

from .models import (
    Contest,
    Judge,
    Participant,
    Score,
    Submission,
    SubmissionReview,
    Team,
    TechStack,
)


@admin.register(Contest)
class ContestAdmin(admin.ModelAdmin):
    list_display = ['slug', 'name', 'status', 'start_at', 'end_at']
    list_filter = ['status']


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ['name', 'contest', 'created_at']
    list_filter = ['contest']


admin.site.register(Participant)
admin.site.register(Submission)
admin.site.register(Judge)
admin.site.register(Score)


@admin.register(TechStack)
class TechStackAdmin(admin.ModelAdmin):
    """정규 기술 스택 목록을 운영자가 고치는 자리.

    전용 관리 화면을 React 로 따로 만들지 않는다 — 목록을 손보는 빈도(대회당 몇 건)에 비해
    과하고, admin 은 이미 있다. 참가자 프로필에 올라왔지만 목록에 없는 태그는 프로필의
    `other_skills` 에 그대로 남으므로, 무엇을 추가해야 하는지는 거기서 확인한다.
    """

    list_display = ['name', 'slug', 'category', 'is_active']
    list_filter = ['category', 'is_active']
    search_fields = ['slug', 'name']
    list_editable = ['is_active']


@admin.register(SubmissionReview)
class SubmissionReviewAdmin(admin.ModelAdmin):
    list_display = ['submission', 'provider', 'model', 'status', 'files_read', 'updated_at']
    list_filter = ['status', 'provider']
