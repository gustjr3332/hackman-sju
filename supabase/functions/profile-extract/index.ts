// 자기소개 원문 → 구조화 프로필 (본인만). backend/contests/profile_extract.py 를 옮겼다.
// 실패해도 오류로 끝내지 않고 프로필을 그대로 돌려준다. 추출 실패가 팀빌딩을 막으면 안 된다.
import { admin, json, serveAuthed } from '../_shared/http.ts';
import { complete } from '../_shared/llm.ts';
import { cleanTags, parseJsonObject, resolveStacks } from '../_shared/logic.ts';

const KNOWN_ROLES = ['frontend', 'backend', 'mobile', 'design', 'data', 'planning', 'ai'];

const prompt = (intro: string) => `다음은 해커톤 참가자가 자기소개로 쓴 글이다. 팀빌딩에 쓸 정보만 뽑아라.

규칙:
- 글에 실제로 적힌 것만 뽑는다. 추측해서 채우지 않는다.
- skills: 기술 스택 이름을 영문 소문자로 (예: react, python, figma).
- roles: 다음 중에서만 고른다 — ${KNOWN_ROLES.join(', ')}. 해당 없으면 빈 배열.
- interests: 만들고 싶어 하는 분야나 주제 (예: 교육, 헬스케어, 게임).
- level: beginner / intermediate / advanced 중 하나. 판단할 근거가 없으면 빈 문자열.

JSON 객체 하나만 출력하고 다른 말은 하지 마라:
{"skills": [], "roles": [], "interests": [], "level": ""}

자기소개:
${intro.trim()}
`;

serveAuthed(async (req, me) => {
  const body = await req.json().catch(() => ({}));
  const { data: profile } = await admin.from('profiles').select('*').eq('id', me.id).single();
  const save = async (patch: Record<string, unknown>) =>
    json((await admin.from('profiles').update(patch).eq('id', me.id).select('*').single()).data);

  if (!profile.intro.trim()) return save({ extraction_status: 'empty' });

  try {
    const result = await complete(prompt(profile.intro), { provider: body.provider, model: body.model });
    const data = parseJsonObject(result.text);
    const { data: stacks } = await admin.from('tech_stacks').select('slug, name, aliases').eq('is_active', true);
    const [skills, other] = resolveStacks(cleanTags(data.skills), stacks ?? []);
    const level = String(data.level ?? '').trim().toLowerCase();
    return save({
      skills,
      other_skills: other,
      interests: cleanTags(data.interests),
      // 모델이 목록 밖 역할을 지어내는 일이 있어 한 번 더 거른다.
      roles: cleanTags(data.roles).filter((r) => KNOWN_ROLES.includes(r)),
      level: ['beginner', 'intermediate', 'advanced'].includes(level) ? level : '',
      extraction_status: 'done',
      extraction_error: '',
      extracted_by: result.model,
      extracted_at: new Date().toISOString(),
    });
  } catch (e) {
    return save({ extraction_status: 'failed', extraction_error: String((e as Error).message).slice(0, 500) });
  }
});
