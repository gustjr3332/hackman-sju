// 백엔드 호출은 전부 여기를 지난다. 화면은 이 파일의 함수 이름과 반환 모양만 믿는다.
// 데이터는 Supabase(RLS 가 권한을 지키는 뷰·테이블·RPC), LLM·GitHub 은 Edge Function 이다.
import { createClient, FunctionsHttpError, type PostgrestError } from '@supabase/supabase-js';
import type {
  Award,
  Contest,
  ContestInput,
  ContestStatus,
  Judge,
  LlmProvider,
  Me,
  Profile,
  Score,
  ScoreboardEntry,
  ScoreRound,
  Submission,
  SubmissionReview,
  Team,
  TeamCandidate,
  TeamRecommendation,
  TechStack,
} from './types';

export const supabase = createClient(
  import.meta.env.VITE_SUPABASE_URL || 'http://127.0.0.1:54321',
  import.meta.env.VITE_SUPABASE_ANON_KEY || ''
);

// ---------- 오류 ----------

/** 화면에 띄울 메시지 외에 상태 코드·응답 본문까지 봐야 하는 호출을 위해 함께 실어 보낸다. */
export class ApiError extends Error {
  status = 0;
  body: unknown = null;
}

// 트리거가 내는 한국어 메시지는 그대로 쓰고, 제약 위반처럼 DB 원문이 오는 것만 바꾼다.
const CONSTRAINT_MESSAGES: Record<string, string> = {
  teams_contest_name_key: '이미 이 대회에 같은 이름의 팀이 있습니다.',
  participants_team_user_key: '이미 참가 중인 팀입니다.',
  awards_contest_rank_key: '이미 같은 등수에 상이 있습니다.',
  contests_pkey: '이미 같은 주소(slug)를 쓰는 대회가 있습니다.',
  contests_end_after_start: '종료 일시는 시작 일시보다 빨라서는 안 됩니다.',
  submissions_team_id_key: '이 팀은 이미 제출물이 있습니다.',
};

function toError(e: PostgrestError | { message: string; code?: string }): ApiError {
  const name = Object.keys(CONSTRAINT_MESSAGES).find((k) => e.message.includes(`"${k}"`));
  let message = name ? CONSTRAINT_MESSAGES[name] : e.message;
  if (/row-level security|permission denied/i.test(e.message)) message = '권한이 없습니다.';
  else if (e.code === '23514' && !name && !/[가-힣]/.test(e.message)) message = '입력 값이 올바르지 않습니다.';
  const err = new ApiError(message);
  err.body = e;
  return err;
}

async function must<T>(q: PromiseLike<{ data: T | null; error: PostgrestError | null }>): Promise<T> {
  const { data, error } = await q;
  if (error) throw toError(error);
  return data as T;
}

/** RLS 로 막힌 수정·삭제는 오류 없이 0행이 된다. 바뀐 행이 없으면 권한이 없는 것으로 본다. */
function one<T>(rows: T[] | null): T {
  if (!rows?.length) throw new ApiError('권한이 없거나 찾을 수 없습니다.');
  return rows[0];
}

/** Edge Function 호출. 실패하면 함수가 준 detail(과 kind)을 담아 던진다. */
export async function callFunction<T>(name: string, body: Record<string, unknown> = {}): Promise<T> {
  const { data, error } = await supabase.functions.invoke(name, { body });
  if (error) {
    const res = error instanceof FunctionsHttpError ? (error.context as Response) : null;
    const detail = res ? await res.json().catch(() => null) : null;
    const err = new ApiError(detail?.detail ?? '요청에 실패했습니다');
    err.status = res?.status ?? 0;
    err.body = detail;
    throw err;
  }
  return data as T;
}

// ---------- 인증 ----------

const USERNAME_KEY = 'hackman_username';

/** 세션이 끝났을 때(갱신 실패·다른 탭 로그아웃) window 에 발생시키는 이벤트 이름. */
export const AUTH_EXPIRED_EVENT = 'hackman:auth-expired';

// 첫 화면을 그릴 때 로그인 여부를 동기로 알아야 해서 아이디를 따로 적어 둔다.
export function getStoredUsername(): string | null {
  return localStorage.getItem(USERNAME_KEY);
}

export function storeUsername(username: string) {
  localStorage.setItem(USERNAME_KEY, username);
}

/** 다른 탭에서 로그인/로그아웃하면 이 탭도 따라가도록 storage 이벤트를 구독한다. */
export function onStoredUsernameChange(handler: (username: string | null) => void): () => void {
  const listener = (event: StorageEvent) => {
    if (event.key === null || event.key === USERNAME_KEY) handler(getStoredUsername());
  };
  window.addEventListener('storage', listener);
  return () => window.removeEventListener('storage', listener);
}

supabase.auth.onAuthStateChange((event) => {
  if (event === 'SIGNED_OUT' && getStoredUsername()) {
    localStorage.removeItem(USERNAME_KEY);
    window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
  }
});

export async function register(username: string, email: string, password: string): Promise<void> {
  const { error } = await supabase.auth.signUp({ email, password, options: { data: { username } } });
  if (!error) return;
  // 가입 트리거가 프로필을 못 만들면(아이디 중복·형식) Auth 는 이 문구만 돌려준다.
  if (/database error saving new user/i.test(error.message)) {
    throw new ApiError('이미 쓰는 아이디이거나 형식이 맞지 않습니다 (영문·숫자·_ . @ + - 만).');
  }
  if (/already registered/i.test(error.message)) throw new ApiError('이미 가입한 이메일입니다.');
  throw new ApiError(error.message);
}

/** 아이디 또는 이메일로 로그인하고 아이디를 돌려준다. 아이디→이메일 조회는 서버(Edge Function)
 * 안에서만 하고 클라이언트로는 절대 넘어오지 않는다 — 자세한 이유는 그 함수 주석 참고. */
export async function login(identifier: string, password: string): Promise<string> {
  const { access_token, refresh_token } = await callFunction<{ access_token: string; refresh_token: string }>(
    'login',
    { identifier, password }
  );
  const { error } = await supabase.auth.setSession({ access_token, refresh_token });
  if (error) throw new ApiError(error.message);
  const me = await fetchMe();
  storeUsername(me.username);
  return me.username;
}

export async function logout() {
  localStorage.removeItem(USERNAME_KEY);
  await supabase.auth.signOut();
}

// 메일 링크가 돌아올 웹 주소. iOS 앱(Capacitor) 안에서는 origin 이 capacitor://localhost 라
// 메일 링크로 쓸 수 없으므로, 앱 빌드에는 VITE_SITE_URL 로 실제 웹 주소를 넣는다.
const SITE_URL = import.meta.env.VITE_SITE_URL || window.location.origin;

/** 비밀번호 재설정 메일. 메일의 링크로 돌아오면 onPasswordRecovery 가 불린다. */
export async function requestPasswordReset(email: string): Promise<void> {
  const { error } = await supabase.auth.resetPasswordForEmail(email, { redirectTo: SITE_URL });
  if (error) throw new ApiError(error.message);
}

export function onPasswordRecovery(handler: () => void): () => void {
  const { data } = supabase.auth.onAuthStateChange((event) => {
    if (event === 'PASSWORD_RECOVERY') handler();
  });
  return () => data.subscription.unsubscribe();
}

export async function setNewPassword(password: string): Promise<string> {
  const { error } = await supabase.auth.updateUser({ password });
  if (error) throw new ApiError(error.message);
  const me = await fetchMe();
  storeUsername(me.username);
  return me.username;
}

export async function fetchMe(): Promise<Me> {
  const { data } = await supabase.auth.getUser();
  if (!data.user) throw new ApiError('로그인이 필요합니다.');
  return must(supabase.from('profiles').select('username, is_staff').eq('id', data.user.id).single());
}

// ---------- 실시간 ----------

/**
 * 대회에 무언가 바뀌면 onChange 를 부른다. 신호에는 데이터가 없으니 화면은 다시 불러온다.
 * 구독이 붙기 전의 변경은 오지 않으므로 붙은 직후에도 한 번 부른다.
 */
export function subscribeContest(slug: string, onChange: () => void): () => void {
  const channel = supabase
    .channel(`contest:${slug}`)
    .on('broadcast', { event: 'changed' }, onChange)
    .subscribe((status) => {
      if (status === 'SUBSCRIBED') onChange();
    });
  return () => {
    supabase.removeChannel(channel);
  };
}

// ---------- contests ----------

export function fetchContests(): Promise<Contest[]> {
  return must(supabase.from('contest_list').select('*').order('start_at', { ascending: false }));
}

export function fetchContest(slug: string): Promise<Contest> {
  return must(supabase.from('contest_list').select('*').eq('slug', slug).single());
}

export async function createContest(data: ContestInput): Promise<Contest> {
  await must(supabase.from('contests').insert(data));
  return fetchContest(data.slug);
}

export async function updateContest(
  slug: string,
  data: Partial<ContestInput> & { status?: ContestStatus }
): Promise<Contest> {
  one(await must(supabase.from('contests').update(data).eq('slug', slug).select('slug')));
  return fetchContest(data.slug ?? slug);
}

/** 대회와 딸린 데이터를 전부 지운다. 되돌릴 수 없으므로 호출부에서 확인 절차를 거친다. */
export async function deleteContest(slug: string): Promise<void> {
  one(await must(supabase.from('contests').delete().eq('slug', slug).select('slug')));
}

const two = (v: number | string | null) => (v === null ? null : Number(v).toFixed(2));

export async function fetchScoreboard(slug: string): Promise<ScoreboardEntry[]> {
  const rows = await must(supabase.rpc('scoreboard', { p_slug: slug }));
  return (rows as ScoreboardEntry[]).map((r) => ({ ...r, average_score: two(r.average_score) }));
}

/** 발표 순서를 제출 시각순으로 (재)배정한다 (운영자 전용). */
export async function assignPresentationOrder(slug: string): Promise<Contest> {
  await must(supabase.rpc('assign_presentation_order', { p_slug: slug }));
  return fetchContest(slug);
}

/** 발표 순서·발표 시간을 팀 단위로 바꾼다 (운영자 전용). 넘기지 않은 값은 그대로 둔다. */
export async function updateTeamPresentation(
  teamId: number,
  data: { presentation_order?: number; presentation_minutes?: number | null }
): Promise<Team> {
  const current = await must<{ presentation_order: number | null; presentation_minutes: number | null }>(
    supabase.from('teams').select('presentation_order, presentation_minutes').eq('id', teamId).single()
  );
  const next = { ...current, ...data };
  return one(await must(supabase.rpc('set_team_schedule', {
    p_team_id: teamId,
    p_order: next.presentation_order,
    p_minutes: next.presentation_minutes,
  }))) as Team;
}

export async function startPresentation(teamId: number): Promise<Team> {
  return one(await must(supabase.rpc('start_presentation', { p_team_id: teamId }))) as Team;
}

export async function endPresentation(teamId: number): Promise<Team> {
  return one(await must(supabase.rpc('end_presentation', { p_team_id: teamId }))) as Team;
}

export async function resetPresentation(teamId: number): Promise<Team> {
  return one(await must(supabase.rpc('reset_presentation', { p_team_id: teamId }))) as Team;
}

// ---------- teams ----------

export function fetchTeams(contestSlug: string): Promise<Team[]> {
  return must(supabase.from('team_list').select('*').eq('contest', contestSlug).order('name'));
}

export async function createTeam(contestSlug: string, name: string): Promise<Team> {
  const row = await must<{ id: number }>(supabase.from('teams').insert({ contest_slug: contestSlug, name }).select('id').single());
  return must(supabase.from('team_list').select('*').eq('id', row.id).single());
}

export async function joinTeam(teamId: number): Promise<void> {
  await must(supabase.from('participants').insert({ team_id: teamId }));
}

// ---------- judges ----------

export function fetchJudges(contestSlug: string): Promise<Judge[]> {
  return must(supabase.from('judge_list').select('*').eq('contest', contestSlug));
}

export async function addJudge(contestSlug: string, username: string): Promise<Judge> {
  return one(await must(supabase.rpc('assign_judge', { p_slug: contestSlug, p_username: username }))) as Judge;
}

export async function removeJudge(judgeId: number): Promise<void> {
  one(await must(supabase.from('judges').delete().eq('id', judgeId).select('id')));
}

// ---------- scores ----------

const withValue = (s: Score) => ({ ...s, value: two(s.value) as string });

/** 이 대회에서 내가 입력한 점수만. 운영자여도 남의 점수는 섞이지 않는다. */
export async function fetchMyScores(contestSlug: string): Promise<Score[]> {
  const rows = await must(supabase.from('score_list').select('*').eq('contest', contestSlug).eq('is_mine', true));
  return (rows as Score[]).map(withValue);
}

/** 같은 라운드에 다시 저장하면 덮어쓴다. 기존 점수 id 는 서버가 찾으므로 쓰지 않는다. */
export async function upsertScore(
  submissionId: number,
  round: ScoreRound,
  _existingId: number | undefined,
  data: { value: string; comment: string }
): Promise<Score> {
  const rows = await must(supabase.rpc('submit_score', {
    p_submission_id: submissionId,
    p_round: round,
    p_value: Number(data.value),
    p_comment: data.comment,
  }));
  return withValue(one(rows as Score[]));
}

// ---------- submissions ----------

const SUBMISSION_COLUMNS = 'id, team:team_id, title, description, link_url, repo_url, submitted_at';

export async function upsertSubmission(
  teamId: number,
  existingId: number | undefined,
  data: { title: string; description: string; link_url: string; repo_url: string }
): Promise<Submission> {
  if (existingId) {
    return one(await must(supabase.from('submissions').update(data).eq('id', existingId).select(SUBMISSION_COLUMNS))) as Submission;
  }
  return must(supabase.from('submissions').insert({ team_id: teamId, ...data }).select(SUBMISSION_COLUMNS).single()) as Promise<Submission>;
}

// ---------- awards (organizer only) ----------

const AWARD_COLUMNS = 'id, contest:contest_slug, rank, title';

export function fetchAwards(contestSlug: string): Promise<Award[]> {
  return must(supabase.from('awards').select(AWARD_COLUMNS).eq('contest_slug', contestSlug).order('rank')) as Promise<Award[]>;
}

export function createAward(contestSlug: string, rank: number, title: string): Promise<Award> {
  return must(supabase.from('awards').insert({ contest_slug: contestSlug, rank, title }).select(AWARD_COLUMNS).single()) as Promise<Award>;
}

export async function updateAward(id: number, title: string): Promise<Award> {
  return one(await must(supabase.from('awards').update({ title }).eq('id', id).select(AWARD_COLUMNS))) as Award;
}

export async function deleteAward(id: number): Promise<void> {
  one(await must(supabase.from('awards').delete().eq('id', id).select('id')));
}

// ---------- 팀빌딩 (프로필 + 추천) ----------

// 참가자가 직접 고칠 수 있는 필드. 나머지(추출 상태·운영자 여부 등)는 DB 가 열 권한으로 막는다.
const PROFILE_EDITABLE = ['intro', 'github_url', 'skills', 'interests', 'roles', 'level', 'looking_for_team'] as const;

export async function fetchMyProfile(): Promise<Profile> {
  const { data } = await supabase.auth.getUser();
  if (!data.user) throw new ApiError('로그인이 필요합니다.');
  return must(supabase.from('profiles').select('*').eq('id', data.user.id).single());
}

export async function updateMyProfile(data: Partial<Profile>): Promise<Profile> {
  const { data: auth } = await supabase.auth.getUser();
  if (!auth.user) throw new ApiError('로그인이 필요합니다.');
  const patch = Object.fromEntries(Object.entries(data).filter(([k]) => (PROFILE_EDITABLE as readonly string[]).includes(k)));
  return must(supabase.from('profiles').update(patch).eq('id', auth.user.id).select('*').single());
}

/** 자기소개를 LLM 으로 구조화한다. 실패해도 예외가 아니라 extraction_status 'failed' 프로필이 온다. */
export function extractMyProfile(provider?: string, model?: string): Promise<Profile> {
  return callFunction('profile-extract', { provider, model });
}

/** 키가 설정된 제공사만 돌아온다. 빈 배열이면 자동 정리 버튼을 숨긴다. */
export function fetchLlmProviders(): Promise<{ providers: LlmProvider[] }> {
  return callFunction('llm-models');
}

/** 이 대회에서 나에게 맞는 팀 순위. 이미 팀이 있으면 빈 배열. */
export function fetchRecommendedTeams(slug: string): Promise<{ teams: TeamRecommendation[] }> {
  return callFunction('recommendations', { contest: slug });
}

/** 이 팀에 맞는, 아직 팀이 없는 사람 순위. 팀원과 운영자만 볼 수 있다. */
export function fetchTeamCandidates(teamId: number): Promise<{ candidates: TeamCandidate[] }> {
  return callFunction('recommendations', { team: teamId });
}

// ---------- 정규 기술 스택 목록 ----------

export async function fetchTechStacks(): Promise<{ stacks: TechStack[] }> {
  const stacks = await must(supabase.from('tech_stacks').select('slug, name, category, aliases').eq('is_active', true).order('category').order('name'));
  return { stacks: stacks as TechStack[] };
}

// ---------- 심사 보조 (제출 저장소 사전 분석) ----------

const PROVIDER_LABEL: Record<string, string> = { anthropic: 'Anthropic', openai: 'OpenAI', google: 'Google' };

/** 이 제출물의 분석 결과 전부. 운영자·배정된 심사위원만 보이고, 그 외에는 빈 목록이다(RLS). */
export async function fetchSubmissionReviews(submissionId: number): Promise<{ reviews: SubmissionReview[] }> {
  const rows = await must(supabase.from('submission_reviews').select('*, submissions(submitted_at)').eq('submission_id', submissionId).order('provider'));
  type Row = SubmissionReview & { submission_id: number; submission_seen_at: string | null; submissions: { submitted_at: string } | null };
  const reviews = (rows as unknown as Row[]).map(({ submissions, submission_id, ...r }) => ({
    ...r,
    submission: submission_id,
    provider_label: PROVIDER_LABEL[r.provider] ?? r.provider,
    // 분석 이후 제출물이 바뀌었으면 낡은 분석이다.
    is_stale: Boolean(r.submission_seen_at && submissions && submissions.submitted_at > r.submission_seen_at),
  }));
  return { reviews };
}

/** 제출물 하나를 분석한다 (운영자 전용). 응답은 '분석 중' 행이고 결과는 다시 불러와 확인한다. */
export function analyzeSubmission(submissionId: number, provider?: string, model?: string): Promise<SubmissionReview> {
  return callFunction('analyze-submission', { submission_id: submissionId, provider, model });
}

/** 대회의 제출물 전체를 한 모델로 분석한다 (운영자 전용, 심사 전에 한 번). */
export function analyzeContestSubmissions(
  slug: string,
  provider?: string,
  model?: string
): Promise<{ queued: number; provider: string; model: string; reviews: SubmissionReview[] }> {
  return callFunction('analyze-submission', { contest: slug, provider, model });
}
