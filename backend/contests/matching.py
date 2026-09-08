"""팀빌딩 추천 — 순위 계산은 전부 규칙 기반이다.

왜 규칙 기반인가:
- 결정적이고, 왜 그 순서인지 설명할 수 있고, 공정성을 검증할 수 있다. 팀 배정은 사회적으로
  눈에 띄는 결과라("왜 나는 저 팀에 못 갔나") 운영자가 답할 수 있어야 한다.
- 팀 모집은 모집중 상태에서만 열리고 그동안 팀이 계속 찬다. 추천은 화면을 열 때마다 다시
  계산되는데, 매번 LLM 을 부르면 워커 1개짜리 서버(`Procfile` 에 `-w` 없음)가 막힌다.
  **추출은 참가자당 1회(llm.py), 매칭은 매번 여기서** — 이 분담이 구조적으로 맞다.

LLM 은 순위에 관여하지 않는다. 상위 몇 건에 한 줄 설명을 붙이는 선택 기능일 뿐이고, 그것이
없어도 추천은 그대로 동작해야 한다.
"""

from .models import Participant, Profile, Team

# 한 팀의 권장 인원. 이보다 적은 팀을 먼저 채우도록 점수에 반영한다.
TARGET_TEAM_SIZE = 4

# 점수 가중치. 합이 100이 되게 두어 결과를 백분율처럼 읽을 수 있다.
WEIGHT_ROLE_GAP = 45      # 팀에 없는 역할을 채우는가 (가장 중요)
WEIGHT_INTEREST = 25      # 관심사가 팀과 겹치는가
WEIGHT_TEAM_ROOM = 20     # 팀에 자리가 남았는가 (작은 팀 우선)
WEIGHT_SKILL_SPREAD = 10  # 스택이 너무 겹치지 않는가


def _normalize(values):
    """대소문자·공백 차이로 같은 스택이 다르게 세어지는 것을 막는다."""
    return {str(v).strip().lower() for v in (values or []) if str(v).strip()}


class TeamSnapshot:
    """한 팀의 현재 구성. 팀마다 프로필을 다시 조회하지 않으려고 미리 모아 둔다."""

    def __init__(self, team, profiles):
        self.team = team
        self.size = len(profiles)
        self.roles = set()
        self.skills = set()
        self.interests = set()
        for p in profiles:
            self.roles |= _normalize(p.roles)
            self.skills |= _normalize(p.skills)
            self.interests |= _normalize(p.interests)


def _score(profile, snapshot):
    """이 사람이 이 팀에 얼마나 맞는지 0~100. 함께 근거 문자열 목록을 돌려준다."""
    roles = _normalize(profile.roles)
    skills = _normalize(profile.skills)
    interests = _normalize(profile.interests)
    reasons = []
    score = 0.0

    # 1. 팀에 없는 역할을 가져오는가. 빈 역할을 채우는 것이 팀 구성에서 가장 값어치가 크다.
    if roles:
        new_roles = roles - snapshot.roles
        ratio = len(new_roles) / len(roles)
        score += WEIGHT_ROLE_GAP * ratio
        if new_roles:
            reasons.append(f"팀에 없는 역할: {', '.join(sorted(new_roles))}")
    else:
        # 역할을 안 적은 사람을 통째로 밀어내지 않는다 — 절반만 준다.
        score += WEIGHT_ROLE_GAP * 0.5

    # 2. 관심사가 겹치는가. 같은 걸 만들고 싶어야 팀이 굴러간다.
    if interests and snapshot.interests:
        shared = interests & snapshot.interests
        score += WEIGHT_INTEREST * (len(shared) / len(interests))
        if shared:
            reasons.append(f"관심사가 겹침: {', '.join(sorted(shared))}")
    else:
        score += WEIGHT_INTEREST * 0.5

    # 3. 자리가 남았는가. 인원이 적은 팀을 먼저 채워 한 팀만 비대해지는 것을 막는다.
    room = max(0, TARGET_TEAM_SIZE - snapshot.size)
    score += WEIGHT_TEAM_ROOM * (room / TARGET_TEAM_SIZE)
    if snapshot.size == 0:
        reasons.append('아직 아무도 없는 팀')
    elif room:
        reasons.append(f'{snapshot.size}명 — {room}자리 남음')

    # 4. 스택이 너무 겹치지 않는가. 전부 같은 스택이면 못 만드는 부분이 생긴다.
    if skills:
        fresh = skills - snapshot.skills
        score += WEIGHT_SKILL_SPREAD * (len(fresh) / len(skills))
    else:
        score += WEIGHT_SKILL_SPREAD * 0.5

    return round(score, 1), reasons


def _snapshots(contest):
    """대회의 모든 팀을 스냅샷으로. 팀 수와 무관하게 쿼리 수가 고정이다."""
    teams = list(Team.objects.filter(contest=contest))
    members = Participant.objects.filter(team__contest=contest).select_related('user')
    profiles = {
        p.user_id: p for p in Profile.objects.filter(user__participations__team__contest=contest)
    }
    by_team = {t.id: [] for t in teams}
    for m in members:
        by_team[m.team_id].append(profiles.get(m.user_id) or Profile(user_id=m.user_id))
    return [TeamSnapshot(t, by_team[t.id]) for t in teams]


def recommend_teams_for_user(contest, user, limit=5):
    """이 사람에게 맞는 팀 순위. 이미 팀이 있으면 빈 목록(추천할 이유가 없다)."""
    if Participant.objects.filter(team__contest=contest, user=user).exists():
        return []
    profile = Profile.objects.filter(user=user).first()
    if profile is None:
        return []

    ranked = []
    for snap in _snapshots(contest):
        if snap.size >= TARGET_TEAM_SIZE:
            continue  # 이미 찬 팀은 추천하지 않는다
        score, reasons = _score(profile, snap)
        ranked.append({
            'team_id': snap.team.id,
            'team_name': snap.team.name,
            'member_count': snap.size,
            'score': score,
            'reasons': reasons,
        })
    ranked.sort(key=lambda r: (-r['score'], r['team_name']))
    return ranked[:limit]


def recommend_users_for_team(contest, team, limit=5):
    """이 팀에 맞는, 아직 팀이 없는 사람 순위."""
    taken = set(
        Participant.objects.filter(team__contest=contest).values_list('user_id', flat=True)
    )
    candidates = Profile.objects.filter(looking_for_team=True).select_related('user')

    snapshot = next((s for s in _snapshots(contest) if s.team.id == team.id), None)
    if snapshot is None:
        return []

    ranked = []
    for profile in candidates:
        if profile.user_id in taken:
            continue
        score, reasons = _score(profile, snapshot)
        ranked.append({
            'username': profile.user.username,
            'skills': profile.skills,
            'roles': profile.roles,
            # 태그만으로는 안 보이는 실제 결과물을 팀이 직접 확인할 수 있게 함께 내려준다.
            'github_url': profile.github_url,
            'score': score,
            'reasons': reasons,
        })
    ranked.sort(key=lambda r: (-r['score'], r['username']))
    return ranked[:limit]
