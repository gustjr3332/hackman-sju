// 심사 도구·프로젝트 상세의 GitHub 열람. backend/contests/github.py 의 GithubProxyView 를 옮겼다.
// 받는 것은 저장소 URL 뿐이고 owner/repo 를 뽑아 서버가 경로를 조립한다. 로그인한 사용자만 쓴다.
import { decodeContent, GithubError, githubFetch, githubGet, quotePath } from '../_shared/github.ts';
import { fail, json, serveAuthed } from '../_shared/http.ts';
import { lastPage, parseGithubRepo } from '../_shared/logic.ts';

const MAX_FILES = 500;

// deno-lint-ignore no-explicit-any
const commitDate = (c: any) => c?.commit?.author?.date ?? c?.commit?.committer?.date ?? null;

serveAuthed(async (req) => {
  const { resource, repo, branch, path } = await req.json().catch(() => ({}));
  const ref = parseGithubRepo(String(repo ?? ''));
  if (!ref) return fail(400, 'GitHub 저장소 URL이 아닙니다.', 'error');
  const base = `/repos/${ref[0]}/${ref[1]}`;

  try {
    switch (resource) {
      case 'repo':
        return json({ default_branch: (await githubGet(base)).default_branch ?? 'main' });
      case 'readme':
        return json({ content: decodeContent(await githubGet(`${base}/readme`)) });
      case 'tree': {
        if (!branch) return fail(400, '브랜치가 필요합니다.', 'error');
        const data = await githubGet(`${base}/git/trees/${encodeURIComponent(branch)}?recursive=1`);
        // deno-lint-ignore no-explicit-any
        const files = (data.tree ?? []).filter((i: any) => i.type === 'blob');
        return json({
          // deno-lint-ignore no-explicit-any
          files: files.slice(0, MAX_FILES).map((f: any) => ({ path: f.path, type: f.type })),
          truncated: Boolean(data.truncated) || files.length > MAX_FILES,
        });
      }
      case 'file': {
        const segments = String(path ?? '').split('/').filter(Boolean);
        if (!segments.length || segments.includes('..')) return fail(400, '잘못된 경로입니다.', 'error');
        return json({ content: decodeContent(await githubGet(`${base}/contents/${quotePath(segments.join('/'))}`)) });
      }
      case 'commits': {
        // 표절 판정이 아니라 첫 커밋 시각이라는 사실만 돌려준다. per_page=1 + Link 의 마지막 페이지 번호.
        const first = await githubFetch(`${base}/commits?per_page=1`);
        const commits = Array.isArray(first.payload) ? first.payload : [];
        if (!commits.length) return json({ first_commit_at: null, latest_commit_at: null, total_commits: 0 });
        const total = lastPage(first.link) ?? 1;
        const oldest = total > 1 ? (await githubGet(`${base}/commits?per_page=1&page=${total}`)).at(-1) : commits[0];
        return json({ first_commit_at: commitDate(oldest), latest_commit_at: commitDate(commits[0]), total_commits: total });
      }
      default:
        return fail(404, '지원하지 않는 요청입니다.', 'error');
    }
  } catch (e) {
    if (e instanceof GithubError) return fail(e.status, e.message, e.kind);
    throw e;
  }
});
