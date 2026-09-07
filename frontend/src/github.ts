// 저장소 열람은 백엔드 프록시(`/api/github/…`)를 거친다.
//
// 예전에는 브라우저가 api.github.com 을 직접 불렀는데, 비인증 한도가 시간당 60회라 같은
// 저장소를 심사위원 여러 명이 반복해서 여는 실제 심사에서 먼저 바닥났다. 지금은 서버가
// 토큰(선택)으로 대신 호출하고 응답을 캐시하므로 한도가 사실상 문제되지 않는다.
// 서버 구현과 캐시 수명은 `backend/contests/github.py` 참고.

import { ApiError, apiGet } from './api';

export interface GithubRepoRef {
  owner: string;
  repo: string;
}

export interface GithubFile {
  path: string;
  type: string;
}

export type GithubErrorKind = 'not-found' | 'rate-limit' | 'error';

export class GithubApiError extends Error {
  kind: GithubErrorKind;
  constructor(kind: GithubErrorKind) {
    super(kind);
    this.kind = kind;
  }
}

const KINDS: GithubErrorKind[] = ['not-found', 'rate-limit', 'error'];

/**
 * 저장소 URL 을 그대로 프록시에 넘긴다. owner/repo 파싱과 검증은 서버에서도 한 번 더 하므로
 * 여기서는 "GitHub 링크인지" 화면에 안내하기 위한 용도로만 쓴다.
 */
export function parseGithubRepo(url: string): GithubRepoRef | null {
  try {
    const parsed = new URL(url);
    if (!/(^|\.)github\.com$/i.test(parsed.hostname)) return null;
    const parts = parsed.pathname.split('/').filter(Boolean);
    if (parts.length < 2) return null;
    const [owner, repoRaw] = parts;
    const repo = repoRaw.replace(/\.git$/i, '');
    if (!owner || !repo) return null;
    return { owner, repo };
  } catch {
    return null;
  }
}

function repoUrl(ref: GithubRepoRef): string {
  return `https://github.com/${ref.owner}/${ref.repo}`;
}

async function proxyGet<T>(resource: string, params: Record<string, string>): Promise<T> {
  const query = new URLSearchParams(params).toString();
  try {
    return await apiGet<T>(`/github/${resource}/?${query}`);
  } catch (err) {
    // 서버가 실패 원인을 kind 로 알려주므로 화면 안내 문구를 그대로 유지할 수 있다.
    const body = err instanceof ApiError ? (err.body as { kind?: string } | null) : null;
    const kind = KINDS.find((k) => k === body?.kind) ?? 'error';
    throw new GithubApiError(kind);
  }
}

export async function fetchDefaultBranch(ref: GithubRepoRef): Promise<string> {
  const data = await proxyGet<{ default_branch: string }>('repo', { repo: repoUrl(ref) });
  return data.default_branch;
}

export async function fetchReadme(ref: GithubRepoRef): Promise<string> {
  const data = await proxyGet<{ content: string }>('readme', { repo: repoUrl(ref) });
  return data.content;
}

export function fetchTree(
  ref: GithubRepoRef,
  branch: string
): Promise<{ files: GithubFile[]; truncated: boolean }> {
  // 블롭만 남기고 개수 상한을 적용하는 것도 서버가 한다 (캐시에 들어가는 크기를 줄인다).
  return proxyGet('tree', { repo: repoUrl(ref), branch });
}

export async function fetchFileContent(ref: GithubRepoRef, path: string): Promise<string> {
  const data = await proxyGet<{ content: string }>('file', { repo: repoUrl(ref), path });
  return data.content;
}
