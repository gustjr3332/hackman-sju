// 심사 보조: 제출 저장소를 미리 읽어 정리해 둔다(운영자 전용). backend/contests/judge_assist.py 를 옮겼다.
//   { submission_id, provider?, model? } → 제출물 하나, { contest, provider?, model? } → 대회 전체
//
// 점수는 제안하지 않는다. 제안 점수를 띄우면 심사위원이 거기에 닻을 내려 결국 모델이 채점하는 것과
// 같아진다. 대신 항목마다 근거 파일 경로를 붙인다.
//
// 응답은 '분석 중' 행으로 바로 돌려주고, 분석은 EdgeRuntime.waitUntil 로 응답 뒤에 돈다. 벽시계
// 150초(무료) 안에 끝나야 해서 파일은 병렬로 읽고, 대회 전체는 제출물마다 이 함수를 따로 불러
// 각자 제한 시간을 갖게 한다.
// deno-lint-ignore-file no-explicit-any
import { decodeContent, GithubError, githubGet, quotePath } from '../_shared/github.ts';
import { admin, fail, json, serveAuthed } from '../_shared/http.ts';
import { availableModels, complete, PROVIDERS } from '../_shared/llm.ts';
import { byPriority, cleanFindings, isSource, parseGithubRepo, parseJsonObject } from '../_shared/logic.ts';

declare const EdgeRuntime: { waitUntil(p: Promise<unknown>): void };

const MAX_FILES = 30;
const MAX_FILE_CHARS = 6000;
const MAX_TOTAL_CHARS = 120_000;
const MAX_README_CHARS = 8000;
const PARALLEL = 6;
const LLM_TIMEOUT_MS = 110_000;
// 저장소 수만 토큰을 읽는 모델은 본문 전에 추론에 예산을 먼저 쓴다. 빠듯하면 본문이 비어 온다.
const MAX_OUTPUT_TOKENS = 8000;

const PROMPT = (title: string, description: string, ctx: any) => `너는 해커톤 심사위원을 돕는 분석가다. 아래 제출물의 저장소 내용을 읽고,
심사위원이 10분 안에 파악해야 할 것을 정리하라.

**절대 점수를 매기거나 제안하지 마라.** 잘했다/못했다는 평가도 하지 마라. 네가 하는 일은
"무엇이 있고 무엇이 없는지"를 사실로 적는 것이고, 판단은 심사위원이 한다.

규칙:
- 저장소에 실제로 있는 코드만 근거로 삼는다. README 의 주장과 코드가 다르면 그 차이를 적는다.
- 모든 항목에 근거 파일 경로(paths)를 붙인다. 경로 없이 주장하지 마라.
- 읽은 범위에서 확인할 수 없으면 그렇게 적는다. 추측해서 채우지 마라.

findings 의 kind 는 셋 중 하나다:
- "implemented": 실제로 동작하는 코드가 있는 기능
- "shell": 이름·라우트·함수는 있는데 본문이 비어 있거나 더미 값을 돌려주는 부분
- "note": 심사위원이 알아야 할 나머지 사실 (외부 API 의존, 하드코딩된 키, 참고할 설계 판단 등)

JSON 객체 하나만 출력하고 다른 말은 하지 마라:
{"summary": "이 프로젝트가 실제로 하는 일 3~5문장",
  "stack": ["확인된 기술 스택"],
  "findings": [{"kind": "implemented", "title": "짧은 제목", "detail": "설명", "paths": ["경로"]}]}

제출물 제목: ${title}
제출물 설명: ${description.slice(0, 2000)}

=== README ===
${ctx.readme || '(README 없음)'}

=== 파일 목록 (${ctx.fileCount}개 중 표시) ===
${ctx.fileList}

=== 소스 내용 ===
${ctx.sources || '(읽을 수 있는 소스 파일이 없음)'}
`;

async function collect(repoUrl: string) {
  const ref = parseGithubRepo(repoUrl);
  if (!ref) throw new Error('GitHub 저장소 URL이 아닙니다.');
  const base = `/repos/${ref[0]}/${ref[1]}`;
  const meta = await githubGet(base);
  const tree = await githubGet(`${base}/git/trees/${encodeURIComponent(meta.default_branch || 'main')}?recursive=1`);
  const readme = await githubGet(`${base}/readme`).then(decodeContent).catch(() => '');

  const all: string[] = (tree.tree ?? []).filter((i: any) => i.type === 'blob' && i.path).map((i: any) => i.path);
  const candidates = all.filter(isSource).sort(byPriority);
  let truncated = Boolean(tree.truncated) || candidates.length > MAX_FILES;

  const picked = candidates.slice(0, MAX_FILES);
  const contents: (string | null)[] = new Array(picked.length).fill(null);
  for (let i = 0; i < picked.length; i += PARALLEL) {
    await Promise.all(picked.slice(i, i + PARALLEL).map(async (p, j) => {
      contents[i + j] = await githubGet(`${base}/contents/${quotePath(p)}`).then(decodeContent).catch(() => null);
    }));
  }

  const sources: string[] = [];
  const read: string[] = [];
  let total = 0;
  picked.forEach((p, i) => {
    let text = contents[i];
    if (text === null) return;
    if (total >= MAX_TOTAL_CHARS) {
      truncated = true;
      return;
    }
    if (text.length > MAX_FILE_CHARS) {
      text = text.slice(0, MAX_FILE_CHARS) + '\n... (이하 생략)';
      truncated = true;
    }
    sources.push(`--- ${p} ---\n${text}`);
    read.push(p);
    total += text.length;
  });

  return {
    readme: readme.slice(0, MAX_README_CHARS),
    fileList: all.slice(0, 200).join('\n'),
    fileCount: all.length,
    sources: sources.join('\n\n'),
    read,
    truncated,
  };
}

async function analyze(review: any, submission: any) {
  const done = (patch: Record<string, unknown>) => admin.from('submission_reviews').update(patch).eq('id', review.id);
  try {
    if (!submission.repo_url) {
      return await done({ status: 'failed', error: '제출물에 GitHub 저장소 주소가 없습니다.', submission_seen_at: new Date().toISOString() });
    }
    let ctx;
    try {
      ctx = await collect(submission.repo_url);
    } catch (e) {
      const msg = e instanceof GithubError ? e.message : (e as Error).message;
      return await done({ status: 'failed', error: `저장소를 읽을 수 없었습니다: ${msg}`, submission_seen_at: new Date().toISOString() });
    }
    const result = await complete(PROMPT(submission.title, submission.description ?? '', ctx), {
      provider: review.provider, model: review.model, maxTokens: MAX_OUTPUT_TOKENS, timeoutMs: LLM_TIMEOUT_MS,
    });
    const data = parseJsonObject(result.text);
    const findings = cleanFindings(data.findings);
    await done({
      status: 'done',
      summary: String(data.summary ?? '').trim(),
      findings,
      stack: (Array.isArray(data.stack) ? data.stack : []).map((s) => String(s).trim()).filter(Boolean).slice(0, 20),
      cited_paths: [...new Set(findings.flatMap((f) => f.paths))],
      truncated: ctx.truncated,
      files_read: ctx.read.length,
      input_tokens: result.inputTokens,
      output_tokens: result.outputTokens,
      error: '',
      submission_seen_at: submission.submitted_at,
    });
  } catch (e) {
    // 여기서 삼키지 않으면 행이 '분석 중'으로 영원히 남는다.
    await done({ status: 'failed', error: String((e as Error).message).slice(0, 500), submission_seen_at: new Date().toISOString() });
  }
}

async function pending(submissionId: number, provider: string, model: string) {
  const { data } = await admin.from('submission_reviews')
    .upsert({ submission_id: submissionId, provider, model, status: 'pending', error: '' }, { onConflict: 'submission_id,provider,model' })
    .select('*').single();
  return { ...data, provider_label: PROVIDERS[provider]?.label ?? provider, is_stale: false };
}

serveAuthed(async (req, me) => {
  if (!me.isStaff) return fail(403, '운영자만 분석을 실행할 수 있습니다.');
  const body = await req.json().catch(() => ({}));
  const provider = body.provider || availableModels()[0]?.provider;
  if (!provider) return fail(400, '설정된 LLM API 키가 없습니다.');
  const model = body.model || PROVIDERS[provider]?.defaultModel;

  if (body.submission_id) {
    const { data: sub } = await admin.from('submissions').select('*').eq('id', body.submission_id).maybeSingle();
    if (!sub) return fail(404, '제출물을 찾을 수 없습니다.');
    const review = await pending(sub.id, provider, model);
    EdgeRuntime.waitUntil(analyze(review, sub));
    return json(review, 202);
  }

  if (body.contest) {
    const { data: subs } = await admin.from('submissions').select('id, teams!inner(contest_slug)')
      .eq('teams.contest_slug', body.contest).neq('repo_url', '');
    if (!subs?.length) return fail(400, '분석할 저장소가 등록된 제출물이 없습니다.');
    const reviews = await Promise.all(subs.map((s: any) => pending(s.id, provider, model)));
    // 제출물마다 따로 불러 각 분석이 150초 제한을 따로 갖게 한다.
    const self = `${Deno.env.get('SUPABASE_URL')}/functions/v1/analyze-submission`;
    const headers = { Authorization: req.headers.get('Authorization')!, 'Content-Type': 'application/json' };
    EdgeRuntime.waitUntil(Promise.all(subs.map((s: any) =>
      fetch(self, { method: 'POST', headers, body: JSON.stringify({ submission_id: s.id, provider, model }) }).then((r) => r.body?.cancel()))));
    return json({ queued: reviews.length, provider, model, reviews }, 202);
  }

  return fail(400, 'submission_id 또는 contest 가 필요합니다.');
});
