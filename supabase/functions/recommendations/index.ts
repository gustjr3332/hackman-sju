// 팀빌딩 추천. 순위는 전부 규칙 기반이라 LLM 키가 없어도 동작한다(backend/contests/matching.py).
// 남의 프로필 전체를 화면에 주지 않으려고 서버에서 계산하고 필요한 필드만 돌려준다.
//   { contest } → 나에게 맞는 팀, { team } → 이 팀에 맞는 사람(팀원·운영자만)
// deno-lint-ignore-file no-explicit-any
import { admin, fail, json, serveAuthed } from '../_shared/http.ts';
import { scoreFit, snapshot, TARGET_TEAM_SIZE, type Tags } from '../_shared/logic.ts';

const LIMIT = 5;
const EMPTY: Tags = { roles: [], skills: [], interests: [] };

async function contestSnapshots(slug: string) {
  const { data: teams } = await admin.from('teams').select('id, name, participants(user_id)').eq('contest_slug', slug);
  const userIds = (teams ?? []).flatMap((t: any) => t.participants.map((p: any) => p.user_id));
  const { data: profiles } = userIds.length
    ? await admin.from('profiles').select('id, roles, skills, interests').in('id', userIds)
    : { data: [] };
  const byId = new Map((profiles ?? []).map((p: any) => [p.id, p as Tags]));
  return (teams ?? []).map((t: any) => ({
    id: t.id as number,
    name: t.name as string,
    members: t.participants.map((p: any) => p.user_id as string),
    snap: snapshot(t.participants.map((p: any) => byId.get(p.user_id) ?? EMPTY)),
  }));
}

serveAuthed(async (req, me) => {
  const { contest, team } = await req.json().catch(() => ({}));

  if (contest) {
    const snaps = await contestSnapshots(contest);
    if (snaps.some((s) => s.members.includes(me.id))) return json({ teams: [] }); // 이미 팀이 있다
    const { data: profile } = await admin.from('profiles').select('roles, skills, interests').eq('id', me.id).single();
    const teams = snaps
      .filter((s) => s.snap.size < TARGET_TEAM_SIZE) // 이미 찬 팀은 추천하지 않는다
      .map((s) => ({ team_id: s.id, team_name: s.name, member_count: s.snap.size, ...scoreFit(profile ?? EMPTY, s.snap) }))
      .sort((a, b) => b.score - a.score || (a.team_name < b.team_name ? -1 : 1))
      .slice(0, LIMIT);
    return json({ teams });
  }

  if (team) {
    const { data: t } = await admin.from('teams').select('contest_slug').eq('id', team).maybeSingle();
    if (!t) return fail(404, '팀을 찾을 수 없습니다.');
    const snaps = await contestSnapshots(t.contest_slug);
    const target = snaps.find((s) => s.id === Number(team))!;
    if (!me.isStaff && !target.members.includes(me.id)) return fail(403, '이 팀의 참가자만 후보를 볼 수 있습니다.');
    const taken = new Set(snaps.flatMap((s) => s.members));
    const { data: people } = await admin.from('profiles')
      .select('id, username, roles, skills, interests, github_url').eq('looking_for_team', true);
    const candidates = (people ?? [])
      .filter((p: any) => !taken.has(p.id))
      .map((p: any) => ({ username: p.username, skills: p.skills, roles: p.roles, github_url: p.github_url, ...scoreFit(p, target.snap) }))
      .sort((a, b) => b.score - a.score || (a.username < b.username ? -1 : 1))
      .slice(0, LIMIT);
    return json({ candidates });
  }

  return fail(400, 'contest 또는 team 이 필요합니다.');
});
