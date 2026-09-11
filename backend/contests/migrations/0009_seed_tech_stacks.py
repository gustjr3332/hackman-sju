"""정규 기술 스택 초기 목록을 넣는다.

빈 목록으로 배포하면 프로필에서 스택 선택 자체가 불가능하다. 목록의 출발점은 GitHub 이
실제로 쓰는 표기(Linguist 언어 + Topics)이고, 시드 이후로는 운영자가 admin 에서 고친다 —
여기 있는 것은 출발점일 뿐 정본이 아니다(`contests/tech_stacks.py` 참고).

되돌리기(reverse)는 아무것도 하지 않는다. 시드 행을 지우면 이미 그 스택을 고른 참가자
프로필의 태그가 말없이 사라지기 때문이다.
"""

from django.db import migrations

from contests.tech_stacks import SEED


def seed(apps, schema_editor):
    TechStack = apps.get_model('contests', 'TechStack')
    existing = set(TechStack.objects.values_list('slug', flat=True))
    TechStack.objects.bulk_create([
        TechStack(slug=slug, name=name, category=category, aliases=list(aliases))
        for slug, name, category, aliases in SEED
        if slug not in existing
    ])


class Migration(migrations.Migration):

    dependencies = [
        ('contests', '0008_techstack_profile_other_skills_submissionreview'),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
