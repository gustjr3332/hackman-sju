from django.apps import AppConfig
from django.db.models.signals import post_delete, post_save


class ContestsConfig(AppConfig):
    name = 'contests'

    def ready(self):
        """정규 스택 목록이 바뀌면 캐시를 즉시 버린다.

        운영자가 대회 중에 admin 에서 스택을 추가하는 상황을 위한 것이다 — TTL 만 믿으면
        방금 추가한 스택이 몇 분간 선택 목록에 안 보인다. 목록을 DB 로 둔 이유가 "대회 당일에
        손을 쓸 수 있게"였으므로 그 자리에서 반영돼야 한다.
        """
        from .models import TechStack
        from .tech_stacks import invalidate

        def _invalidate(sender, **kwargs):
            invalidate()

        post_save.connect(_invalidate, sender=TechStack, dispatch_uid='tech-stack-cache')
        post_delete.connect(_invalidate, sender=TechStack, dispatch_uid='tech-stack-cache')
