"""이미 저장된 자유 문자열 프로필 스택을 정규 목록에 맞춘다.

`Profile.skills` 는 지금까지 참가자가 쉼표로 직접 친 자유 문자열이었다. 그대로 두면 정규
목록으로 바꾼 뒤에도 옛 태그가 목록 밖 값으로 남아 매칭이 계속 어긋난다.

매핑되지 않는 값은 **버리지 않고** `other_skills` 로 옮긴다 — 참가자가 실제로 쓴 기술이
사라지면 안 되고, 운영자가 목록에 무엇을 추가해야 하는지도 이 값으로만 알 수 있다.
되돌리기는 하지 않는다(원문을 복원할 방법이 없고, 복원해야 할 이유도 없다).
"""

from django.db import migrations

from contests.tech_stacks import _fold


def normalize(apps, schema_editor):
    TechStack = apps.get_model('contests', 'TechStack')
    Profile = apps.get_model('contests', 'Profile')

    index = {}
    for stack in TechStack.objects.all():
        index[_fold(stack.slug)] = stack.slug
        index[_fold(stack.name)] = stack.slug
        for alias in stack.aliases or []:
            index.setdefault(_fold(alias), stack.slug)

    changed = []
    for profile in Profile.objects.exclude(skills=[]):
        known, unknown = [], list(profile.other_skills or [])
        for raw in profile.skills or []:
            text = str(raw).strip()
            if not text:
                continue
            slug = index.get(_fold(text))
            if slug:
                if slug not in known:
                    known.append(slug)
            elif text not in unknown:
                unknown.append(text)
        if known != (profile.skills or []) or unknown != (profile.other_skills or []):
            profile.skills = known
            profile.other_skills = unknown
            changed.append(profile)
    if changed:
        Profile.objects.bulk_update(changed, ['skills', 'other_skills'])


class Migration(migrations.Migration):

    dependencies = [
        ('contests', '0009_seed_tech_stacks'),
    ]

    operations = [
        migrations.RunPython(normalize, migrations.RunPython.noop),
    ]
