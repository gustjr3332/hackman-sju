// 외부 호출이 없는 규칙들. backend/contests 의 matching.py·tech_stacks.py·judge_assist.py·github.py
// 에서 옮겨 왔고, logic_test.ts 가 같은 동작을 확인한다.

// ---------------------------------------------------------------- 스택 별칭

/** 비교용 표기 정규화. `React.js` / `react-js` / `react js` 를 같은 것으로 본다. */
export function fold(value: unknown): string {
  return String(value ?? '').trim().toLowerCase().replace(/[\s.\-_/]/g, '');
}

export interface Stack {
  slug: string;
  name: string;
  aliases: string[];
}

/** 자유 문자열 → [정규 slug, 매핑 못 한 원문]. 매핑 못 한 값은 버리지 않는다. */
export function resolveStacks(values: unknown, stacks: Stack[]): [string[], string[]] {
  const index = new Map<string, string>();
  for (const s of stacks) {
    index.set(fold(s.slug), s.slug);
    index.set(fold(s.name), s.slug);
    for (const a of s.aliases ?? []) if (!index.has(fold(a))) index.set(fold(a), s.slug);
  }
  const known: string[] = [];
  const unknown: string[] = [];
  for (const raw of Array.isArray(values) ? values : []) {
    const text = String(raw).trim();
    if (!text) continue;
    const slug = index.get(fold(text));
    if (slug) {
      if (!known.includes(slug)) known.push(slug);
    } else if (!unknown.some((u) => u.toLowerCase() === text.toLowerCase())) {
      unknown.push(text);
    }
  }
  return [known, unknown];
}

/** 태그 목록 정리: 소문자, 중복 제거, 상한. */
export function cleanTags(values: unknown, limit = 12): string[] {
  if (!Array.isArray(values)) return [];
  const out: string[] = [];
  for (const v of values) {
    const tag = String(v).trim().toLowerCase();
    if (tag && !out.includes(tag)) out.push(tag);
    if (out.length >= limit) break;
  }
  return out;
}

/** 모델 응답에서 JSON 객체를 꺼낸다(```json 펜스·앞뒤 설명을 견딘다). */
export function parseJsonObject(text: string): Record<string, unknown> {
  if (!text) throw new Error('빈 응답');
  const fenced = text.match(/```(?:json)?\s*([\s\S]*?)```/);
  const candidate = fenced ? fenced[1] : text;
  const start = candidate.indexOf('{');
  const end = candidate.lastIndexOf('}');
  if (start === -1 || end <= start) throw new Error('응답에서 JSON 을 찾지 못했습니다.');
  try {
    return JSON.parse(candidate.slice(start, end + 1));
  } catch (e) {
    throw new Error(`JSON 파싱 실패: ${(e as Error).message}`);
  }
}

// ---------------------------------------------------------------- 팀 추천 (규칙 기반)

export const TARGET_TEAM_SIZE = 4;
const WEIGHT_ROLE_GAP = 45;
const WEIGHT_INTEREST = 25;
const WEIGHT_TEAM_ROOM = 20;
const WEIGHT_SKILL_SPREAD = 10;

export interface Tags {
  roles: string[];
  skills: string[];
  interests: string[];
}

const norm = (xs: string[] | null | undefined) =>
  new Set((xs ?? []).map((x) => String(x).trim().toLowerCase()).filter(Boolean));

export function snapshot(members: Tags[]) {
  const s = { size: members.length, roles: new Set<string>(), skills: new Set<string>(), interests: new Set<string>() };
  for (const m of members) {
    norm(m.roles).forEach((x) => s.roles.add(x));
    norm(m.skills).forEach((x) => s.skills.add(x));
    norm(m.interests).forEach((x) => s.interests.add(x));
  }
  return s;
}

/** 이 사람이 이 팀에 얼마나 맞는지 0~100 과 근거. matching.py `_score` 와 같다. */
export function scoreFit(person: Tags, team: ReturnType<typeof snapshot>): { score: number; reasons: string[] } {
  const roles = norm(person.roles);
  const skills = norm(person.skills);
  const interests = norm(person.interests);
  const reasons: string[] = [];
  let score = 0;

  if (roles.size) {
    const fresh = [...roles].filter((r) => !team.roles.has(r)).sort();
    score += WEIGHT_ROLE_GAP * (fresh.length / roles.size);
    if (fresh.length) reasons.push(`팀에 없는 역할: ${fresh.join(', ')}`);
  } else {
    score += WEIGHT_ROLE_GAP * 0.5;
  }

  if (interests.size && team.interests.size) {
    const shared = [...interests].filter((i) => team.interests.has(i)).sort();
    score += WEIGHT_INTEREST * (shared.length / interests.size);
    if (shared.length) reasons.push(`관심사가 겹침: ${shared.join(', ')}`);
  } else {
    score += WEIGHT_INTEREST * 0.5;
  }

  const room = Math.max(0, TARGET_TEAM_SIZE - team.size);
  score += WEIGHT_TEAM_ROOM * (room / TARGET_TEAM_SIZE);
  if (team.size === 0) reasons.push('아직 아무도 없는 팀');
  else if (room) reasons.push(`${team.size}명 — ${room}자리 남음`);

  if (skills.size) {
    const fresh = [...skills].filter((s) => !team.skills.has(s));
    score += WEIGHT_SKILL_SPREAD * (fresh.length / skills.size);
  } else {
    score += WEIGHT_SKILL_SPREAD * 0.5;
  }

  return { score: Math.round(score * 10) / 10, reasons };
}

// ---------------------------------------------------------------- GitHub

const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]*$/;

/** 저장소 URL → [owner, repo]. GitHub 저장소가 아니면 null. */
export function parseGithubRepo(url: string): [string, string] | null {
  let u: URL;
  try {
    u = new URL(url);
  } catch {
    return null;
  }
  if (!['http:', 'https:'].includes(u.protocol)) return null;
  const host = u.hostname.toLowerCase();
  if (host !== 'github.com' && !host.endsWith('.github.com')) return null;
  const parts = u.pathname.split('/').filter(Boolean);
  if (parts.length < 2) return null;
  const owner = parts[0];
  const repo = parts[1].replace(/\.git$/i, '');
  return NAME_RE.test(owner) && NAME_RE.test(repo) ? [owner, repo] : null;
}

/** 페이지네이션 Link 헤더의 마지막 페이지 번호. URL 은 따라가지 않고 번호만 쓴다. */
export function lastPage(link: string): number | null {
  const m = (link || '').match(/[?&]page=(\d+)>;\s*rel="last"/);
  return m ? Number(m[1]) : null;
}

// ---------------------------------------------------------------- 심사 보조: 읽을 파일 고르기

const SOURCE_SUFFIXES = [
  '.py', '.js', '.jsx', '.ts', '.tsx', '.java', '.kt', '.swift', '.dart', '.go', '.rs', '.rb', '.php',
  '.c', '.h', '.cpp', '.cs', '.m', '.scala', '.ex', '.exs', '.vue', '.svelte', '.html', '.css', '.scss',
  '.sql', '.sh', '.yml', '.yaml', '.toml',
];
const MANIFEST_NAMES = [
  'package.json', 'requirements.txt', 'pyproject.toml', 'build.gradle', 'build.gradle.kts', 'pom.xml',
  'go.mod', 'cargo.toml', 'gemfile', 'composer.json', 'pubspec.yaml', 'dockerfile', 'docker-compose.yml',
  'procfile',
];
const EXCLUDE_PARTS = [
  'node_modules/', 'vendor/', 'dist/', 'build/', '.venv/', 'venv/', '__pycache__/', 'migrations/', '.next/',
  'coverage/', 'site-packages/', '.git/',
];
const EXCLUDE_NAMES = [
  'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'poetry.lock', 'gemfile.lock', 'composer.lock', 'cargo.lock',
];

const baseName = (p: string) => p.toLowerCase().split('/').pop() ?? '';

export function isSource(path: string): boolean {
  const lower = path.toLowerCase();
  if (EXCLUDE_PARTS.some((part) => lower.includes(part))) return false;
  const name = baseName(path);
  if (EXCLUDE_NAMES.includes(name)) return false;
  return MANIFEST_NAMES.includes(name) || SOURCE_SUFFIXES.some((s) => lower.endsWith(s));
}

/** 먼저 읽을 순서: 매니페스트 → 얕은 경로 → 사전순. 상한에 잘려도 뼈대부터 들어간다. */
export function byPriority(a: string, b: string): number {
  const key = (p: string) => [MANIFEST_NAMES.includes(baseName(p)) ? 0 : 1, p.split('/').length - 1] as const;
  const [ka, kb] = [key(a), key(b)];
  return ka[0] - kb[0] || ka[1] - kb[1] || (a.toLowerCase() < b.toLowerCase() ? -1 : 1);
}

export interface Finding {
  kind: 'implemented' | 'shell' | 'note';
  title: string;
  detail: string;
  paths: string[];
}

/** 모델이 돌려준 항목을 화면이 믿고 쓸 수 있는 모양으로. 경로 없는 항목도 남긴다. */
export function cleanFindings(values: unknown, limit = 20): Finding[] {
  if (!Array.isArray(values)) return [];
  const kinds = ['implemented', 'shell', 'note'];
  return values.slice(0, limit).filter((v) => v && typeof v === 'object').map((v) => {
    const item = v as Record<string, unknown>;
    const kind = String(item.kind ?? '').trim().toLowerCase();
    return {
      kind: (kinds.includes(kind) ? kind : 'note') as Finding['kind'],
      title: String(item.title ?? '').trim().slice(0, 200),
      detail: String(item.detail ?? '').trim().slice(0, 2000),
      paths: Array.isArray(item.paths) ? item.paths.map((p) => String(p).trim()).filter(Boolean).slice(0, 10) : [],
    };
  });
}
