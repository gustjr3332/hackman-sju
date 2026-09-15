// GitHub 읽기. backend/contests/github.py 를 옮겼다. 응답은 github_cache 테이블에 30분 담아 둔다
// (심사위원 여럿이 같은 저장소를 반복해서 열기 때문에). 토큰은 서버에만 있다.
import { admin } from './http.ts';

const API = 'https://api.github.com';
const CACHE_SECONDS = 1800;

export class GithubError extends Error {
  constructor(public kind: 'not-found' | 'rate-limit' | 'error', public status: number) {
    super({
      'not-found': '저장소를 찾을 수 없습니다 (비공개이거나 삭제된 저장소).',
      'rate-limit': 'GitHub 요청 한도에 걸렸습니다. 잠시 뒤 다시 시도해 주세요.',
      error: 'GitHub에서 정보를 가져오지 못했습니다.',
    }[kind]);
  }
}

/** `api.github.com{path}` 를 GET. 경로는 항상 서버가 조립한다(임의 URL 을 열지 않는다). */
export async function githubFetch(path: string): Promise<{ payload: any; link: string }> {
  const { data: hit } = await admin.from('github_cache').select('payload, link, expires_at').eq('key', path).maybeSingle();
  if (hit && new Date(hit.expires_at) > new Date()) return { payload: hit.payload, link: hit.link };

  const headers: Record<string, string> = {
    Accept: 'application/vnd.github+json',
    'User-Agent': 'hackman-judging-tool',
    'X-GitHub-Api-Version': '2022-11-28',
  };
  const token = Deno.env.get('GITHUB_TOKEN');
  if (token) headers.Authorization = `Bearer ${token}`;

  let res: Response;
  try {
    res = await fetch(`${API}${path}`, { headers, signal: AbortSignal.timeout(10_000) });
  } catch {
    throw new GithubError('error', 502);
  }
  if (res.status === 404) throw new GithubError('not-found', 404);
  if (res.status === 403 || res.status === 429) throw new GithubError('rate-limit', 429);
  if (!res.ok) throw new GithubError('error', 502);

  const payload = await res.json();
  const link = res.headers.get('Link') ?? '';
  await admin.from('github_cache').upsert({
    key: path, payload, link, expires_at: new Date(Date.now() + CACHE_SECONDS * 1000).toISOString(),
  });
  return { payload, link };
}

export const githubGet = async (path: string) => (await githubFetch(path)).payload;

export function decodeContent(payload: { content?: string; encoding?: string }): string {
  if (!payload?.content || payload.encoding !== 'base64') throw new GithubError('error', 502);
  const bytes = Uint8Array.from(atob(payload.content.replace(/\n/g, '')), (c) => c.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

export const quotePath = (p: string) => p.split('/').filter(Boolean).map(encodeURIComponent).join('/');
