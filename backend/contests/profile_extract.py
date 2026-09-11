"""자유 서술 → 구조화 프로필. LLM 이 이 기능에서 실제로 값어치를 내는 유일한 지점이다.

참가자는 체크박스 스무 개를 채우지 않는다. "웹 프론트 좀 했고 파이썬도 조금 압니다,
디자인도 관심 있어요" 같은 한 문단을 받아 태그로 바꾸는 일은 규칙으로 못 하고 LLM 이
정확히 잘한다. 참가자당 1회, 짧은 입력·작은 JSON 출력.

순위 계산에는 관여하지 않는다 — 그건 matching.py 의 규칙이 한다.
"""

from django.utils import timezone

from .llm import LlmError, complete
from .llm.base import parse_json_object
from .models import Profile
from .tech_stacks import resolve

# 태그를 정해진 목록에 맞춘다. 자유 문자열이면 "React"/"리액트"/"react.js" 가 다 다른 태그가
# 되어 매칭이 조용히 망가진다. 목록에 없는 것은 other_skills 로 따로 받아 버리지 않는다.
KNOWN_ROLES = ['frontend', 'backend', 'mobile', 'design', 'data', 'planning', 'ai']

PROMPT = """다음은 해커톤 참가자가 자기소개로 쓴 글이다. 팀빌딩에 쓸 정보만 뽑아라.

규칙:
- 글에 실제로 적힌 것만 뽑는다. 추측해서 채우지 않는다.
- skills: 기술 스택 이름을 영문 소문자로 (예: react, python, figma).
- roles: 다음 중에서만 고른다 — {roles}. 해당 없으면 빈 배열.
- interests: 만들고 싶어 하는 분야나 주제 (예: 교육, 헬스케어, 게임).
- level: beginner / intermediate / advanced 중 하나. 판단할 근거가 없으면 빈 문자열.

JSON 객체 하나만 출력하고 다른 말은 하지 마라:
{{"skills": [], "roles": [], "interests": [], "level": ""}}

자기소개:
{intro}
"""


def build_prompt(intro):
    return PROMPT.format(roles=', '.join(KNOWN_ROLES), intro=intro.strip())


def extract_profile(profile, provider=None, model=None):
    """프로필의 `intro` 를 구조화해 같은 레코드에 채운다.

    실패해도 예외를 밖으로 내보내지 않는다 — 추출이 안 됐다고 프로필 저장이나 팀빌딩 화면이
    막히면 안 된다. 실패는 상태로 남기고, 참가자가 직접 태그를 채워 넣을 수 있다.
    """
    if not profile.intro.strip():
        profile.extraction_status = Profile.ExtractionStatus.EMPTY
        profile.save(update_fields=['extraction_status'])
        return profile

    try:
        result = complete(build_prompt(profile.intro), provider=provider, model=model)
        data = parse_json_object(result.text)
    except LlmError as exc:
        profile.extraction_status = Profile.ExtractionStatus.FAILED
        profile.extraction_error = str(exc)[:500]
        profile.save(update_fields=['extraction_status', 'extraction_error'])
        return profile

    # 정규 목록(TechStack)에 맞춘다. 매핑 안 된 값은 버리지 않고 other_skills 로 남긴다 —
    # 버리면 참가자가 실제로 쓴 기술이 사라지고, 운영자가 목록에 무엇을 추가해야 하는지도
    # 알 수 없게 된다.
    profile.skills, profile.other_skills = resolve(_clean(data.get('skills')))
    profile.interests = _clean(data.get('interests'))
    # 모델이 목록 밖의 역할을 지어내는 일이 있어 여기서 한 번 더 거른다.
    profile.roles = [r for r in _clean(data.get('roles')) if r in KNOWN_ROLES]
    level = str(data.get('level') or '').strip().lower()
    profile.level = level if level in Profile.Level.values else ''
    profile.extraction_status = Profile.ExtractionStatus.DONE
    profile.extraction_error = ''
    profile.extracted_by = f'{result.model}'
    profile.extracted_at = timezone.now()
    profile.save()
    return profile


def _clean(values, limit=12):
    if not isinstance(values, list):
        return []
    seen, out = set(), []
    for v in values:
        tag = str(v).strip().lower()
        if tag and tag not in seen:
            seen.add(tag)
            out.append(tag)
        if len(out) >= limit:
            break
    return out
