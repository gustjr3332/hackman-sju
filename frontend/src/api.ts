import type {
  AuthTokens,
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

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000/api';

const ACCESS_TOKEN_KEY = 'webclaude_access_token';
const REFRESH_TOKEN_KEY = 'webclaude_refresh_token';
const USERNAME_KEY = 'webclaude_username';

/** 리프레시까지 실패해 세션이 끝났을 때 window 에 발생시키는 이벤트 이름. */
export const AUTH_EXPIRED_EVENT = 'webclaude:auth-expired';

export function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_TOKEN_KEY);
}

export function getStoredUsername(): string | null {
  return localStorage.getItem(USERNAME_KEY);
}

export function storeUsername(username: string) {
  localStorage.setItem(USERNAME_KEY, username);
}

/** 다른 탭에서 로그인/로그아웃하면 이 탭도 따라가도록 storage 이벤트를 구독한다. 해제 함수를 돌려준다. */
export function onStoredUsernameChange(handler: (username: string | null) => void): () => void {
  const listener = (event: StorageEvent) => {
    if (event.key === null || event.key === USERNAME_KEY) handler(getStoredUsername());
  };
  window.addEventListener('storage', listener);
  return () => window.removeEventListener('storage', listener);
}

export function clearAuth() {
  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
  localStorage.removeItem(USERNAME_KEY);
  clearConditionalCache();
}

// 동시에 여러 요청이 401 을 받아도 리프레시는 한 번만 보낸다.
let refreshInFlight: Promise<boolean> | null = null;

function refreshAccessToken(): Promise<boolean> {
  if (refreshInFlight) return refreshInFlight;
  const refresh = localStorage.getItem(REFRESH_TOKEN_KEY);
  if (!refresh) return Promise.resolve(false);

  refreshInFlight = fetch(`${API_BASE_URL}/auth/token/refresh/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh }),
  })
    .then(async (res) => {
      if (!res.ok) return false;
      const data = (await res.json()) as { access: string; refresh?: string };
      localStorage.setItem(ACCESS_TOKEN_KEY, data.access);
      if (data.refresh) localStorage.setItem(REFRESH_TOKEN_KEY, data.refresh);
      return true;
    })
    .catch(() => false)
    .finally(() => {
      refreshInFlight = null;
    });
  return refreshInFlight;
}

async function send(path: string, options: RequestInit = {}, allowRefresh = true): Promise<Response> {
  const token = getAccessToken();
  const headers = new Headers(options.headers);
  headers.set('Content-Type', 'application/json');
  if (token) headers.set('Authorization', `Bearer ${token}`);

  const res = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });

  if (res.status === 401 && token && allowRefresh) {
    if (await refreshAccessToken()) {
      return send(path, options, false);
    }
    clearAuth();
    window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
    throw new Error('로그인이 만료되었습니다. 다시 로그인해 주세요.');
  }

  // 304 는 "바뀐 게 없다"는 정상 응답이라 ok 가 false 여도 에러가 아니다 (conditionalGet 참고).
  if (!res.ok && res.status !== 304) {
    const detail = await res.json().catch(() => null);
    const message =
      (detail && (detail.detail || Object.values(detail)[0])) || `요청에 실패했습니다 (${res.status})`;
    const error = new ApiError(Array.isArray(message) ? message[0] : String(message));
    error.status = res.status;
    error.body = detail;
    throw error;
  }
  return res;
}

/** 화면에 띄울 메시지 외에 상태 코드·응답 본문까지 봐야 하는 호출을 위해 함께 실어 보낸다. */
export class ApiError extends Error {
  status = 0;
  body: unknown = null;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await send(path, options);
  if (res.status === 204) return undefined as T;
  return res.json();
}

/** 다른 모듈이 같은 인증·에러 처리로 백엔드를 부를 때 쓰는 GET 래퍼 (github.ts). */
export function apiGet<T>(path: string): Promise<T> {
  return request<T>(path);
}

/**
 * 마지막으로 받은 응답과 그 ETag. 폴링이 같은 경로를 반복해서 부르므로, 서버가
 * "안 바뀜(304)"이라고 하면 본문을 받지 않고 여기 있는 값을 그대로 돌려준다.
 */
const conditionalCache = new Map<string, { etag: string; data: unknown }>();

/**
 * 조건부 GET. 이전 응답의 ETag 를 `If-None-Match` 로 보내고, 304 면 이전 데이터를 **같은
 * 객체 참조로** 돌려준다 — 참조가 그대로라 React 가 재렌더까지 건너뛴다.
 */
async function conditionalGet<T>(path: string): Promise<T> {
  const cached = conditionalCache.get(path);
  const res = await send(path, cached ? { headers: { 'If-None-Match': cached.etag } } : {});
  if (res.status === 304 && cached) return cached.data as T;

  const data = (await res.json()) as T;
  const etag = res.headers.get('ETag');
  if (etag) conditionalCache.set(path, { etag, data });
  else conditionalCache.delete(path);
  return data;
}

/** 계정이 바뀌면 같은 경로라도 응답이 달라지므로(예: 결선 순위 가시성) 캐시를 버린다. */
function clearConditionalCache() {
  conditionalCache.clear();
}

export async function register(username: string, email: string, password: string): Promise<void> {
  await request('/auth/register/', {
    method: 'POST',
    body: JSON.stringify({ username, email, password }),
  });
}

export async function login(username: string, password: string): Promise<AuthTokens> {
  const tokens = await request<AuthTokens>('/auth/token/', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  });
  localStorage.setItem(ACCESS_TOKEN_KEY, tokens.access);
  localStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh);
  storeUsername(username);
  clearConditionalCache();
  return tokens;
}

export function logout() {
  clearAuth();
}

export function fetchMe(): Promise<Me> {
  return request('/auth/me/');
}

// ---------- contests ----------

export function fetchContests(): Promise<Contest[]> {
  return request('/contests/');
}

export function fetchContest(slug: string): Promise<Contest> {
  return request(`/contests/${slug}/`);
}

export function createContest(data: ContestInput): Promise<Contest> {
  return request('/contests/', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function updateContest(
  slug: string,
  data: Partial<ContestInput> & { status?: ContestStatus }
): Promise<Contest> {
  return request(`/contests/${slug}/`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

/**
 * 대회와 딸린 데이터(팀·참가자·제출물·심사위원·점수·시상)를 전부 지운다. 되돌릴 수 없으므로
 * 호출부에서 반드시 확인 절차를 거친 뒤에 부른다.
 */
export function deleteContest(slug: string): Promise<void> {
  return request(`/contests/${slug}/`, { method: 'DELETE' });
}

/**
 * 5초마다 불리는 유일한 집계 엔드포인트라 조건부 GET 을 쓴다. 순위가 그대로면 서버가 304 만
 * 돌려주므로 본문 전송·파싱·재렌더가 전부 없어진다.
 */
export function fetchScoreboard(slug: string): Promise<ScoreboardEntry[]> {
  return conditionalGet(`/contests/${slug}/scoreboard/`);
}

/**
 * 발표 순서를 제출 시각순으로 (재)배정한다 (운영자 전용). 시작 시각은 정하지 않는다 —
 * 발표는 운영자가 팀마다 "발표 시작"을 눌러야 시작된다.
 */
export function assignPresentationOrder(slug: string): Promise<Contest> {
  return request(`/contests/${slug}/assign_presentation_order/`, { method: 'POST' });
}

/** 발표 순서·발표 시간을 팀 단위로 바꾼다 (운영자 전용). */
export function updateTeamPresentation(
  teamId: number,
  data: { presentation_order?: number; presentation_minutes?: number | null }
): Promise<Team> {
  return request(`/teams/${teamId}/`, { method: 'PATCH', body: JSON.stringify(data) });
}

/** 이 팀의 발표를 지금 시작한다 (운영자 전용). 아직 안 끝난 다른 팀은 자동으로 종료된다. */
export function startPresentation(teamId: number): Promise<Team> {
  return request(`/teams/${teamId}/start_presentation/`, { method: 'POST' });
}

/** 발표를 끝낸다 (운영자 전용). 남은 시간이 있어도 타이머가 멈춘다. */
export function endPresentation(teamId: number): Promise<Team> {
  return request(`/teams/${teamId}/end_presentation/`, { method: 'POST' });
}

/** 시작/종료 기록을 지운다 (운영자 전용) — 실수로 눌렀을 때 되돌린다. */
export function resetPresentation(teamId: number): Promise<Team> {
  return request(`/teams/${teamId}/reset_presentation/`, { method: 'POST' });
}

// ---------- teams ----------

export function fetchTeams(contestSlug: string): Promise<Team[]> {
  return request(`/teams/?contest=${contestSlug}`);
}

export function createTeam(contestSlug: string, name: string): Promise<Team> {
  return request('/teams/', {
    method: 'POST',
    body: JSON.stringify({ contest: contestSlug, name }),
  });
}

export function joinTeam(teamId: number): Promise<void> {
  return request(`/teams/${teamId}/join/`, { method: 'POST' });
}

// ---------- judges ----------

export function fetchJudges(contestSlug: string): Promise<Judge[]> {
  return request(`/judges/?contest=${contestSlug}`);
}

export function addJudge(contestSlug: string, username: string): Promise<Judge> {
  return request('/judges/', {
    method: 'POST',
    body: JSON.stringify({ contest: contestSlug, username }),
  });
}

export function removeJudge(judgeId: number): Promise<void> {
  return request(`/judges/${judgeId}/`, { method: 'DELETE' });
}

// ---------- scores ----------

/** 이 대회에서 내가 입력한 점수만. 운영자여도 남의 점수는 섞이지 않는다 (mine=1). */
export function fetchMyScores(contestSlug: string): Promise<Score[]> {
  return request(`/scores/?contest=${contestSlug}&mine=1`);
}

export function upsertScore(
  submissionId: number,
  round: ScoreRound,
  existingId: number | undefined,
  data: { value: string; comment: string }
): Promise<Score> {
  if (existingId) {
    return request(`/scores/${existingId}/`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    });
  }
  return request('/scores/', {
    method: 'POST',
    body: JSON.stringify({ submission: submissionId, round, ...data }),
  });
}

// ---------- submissions ----------

export function upsertSubmission(
  teamId: number,
  existingId: number | undefined,
  data: { title: string; description: string; link_url: string; repo_url: string }
): Promise<Submission> {
  if (existingId) {
    return request(`/submissions/${existingId}/`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    });
  }
  return request('/submissions/', {
    method: 'POST',
    body: JSON.stringify({ team: teamId, ...data }),
  });
}

// ---------- awards (organizer only) ----------

export function fetchAwards(contestSlug: string): Promise<Award[]> {
  return request(`/awards/?contest=${contestSlug}`);
}

export function createAward(contestSlug: string, rank: number, title: string): Promise<Award> {
  return request('/awards/', {
    method: 'POST',
    body: JSON.stringify({ contest: contestSlug, rank, title }),
  });
}

export function updateAward(id: number, title: string): Promise<Award> {
  return request(`/awards/${id}/`, { method: 'PATCH', body: JSON.stringify({ title }) });
}

export function deleteAward(id: number): Promise<void> {
  return request(`/awards/${id}/`, { method: 'DELETE' });
}


// ---------- 팀빌딩 (프로필 + 추천) ----------

/** 내 프로필. 서버가 없으면 만들어서 돌려주므로 생성 호출이 따로 없다. */
export function fetchMyProfile(): Promise<Profile> {
  return request('/profile/');
}

export function updateMyProfile(data: Partial<Profile>): Promise<Profile> {
  return request('/profile/', { method: 'PATCH', body: JSON.stringify(data) });
}

/**
 * 자기소개 원문을 LLM 으로 구조화한다. 실패해도 예외가 아니라 `extraction_status: 'failed'`
 * 인 프로필이 돌아온다 — 추출 실패가 팀빌딩을 막지 않는다.
 */
export function extractMyProfile(provider?: string, model?: string): Promise<Profile> {
  return request('/profile/extract/', {
    method: 'POST',
    body: JSON.stringify({ provider, model }),
  });
}

/** 키가 설정된 제공사만 돌아온다. 빈 배열이면 자동 정리 버튼을 숨긴다. */
export function fetchLlmProviders(): Promise<{ providers: LlmProvider[] }> {
  return request('/llm/models/');
}

/** 이 대회에서 나에게 맞는 팀 순위. 이미 팀이 있으면 빈 배열. */
export function fetchRecommendedTeams(slug: string): Promise<{ teams: TeamRecommendation[] }> {
  return request(`/contests/${slug}/recommended_teams/`);
}

/** 이 팀에 맞는, 아직 팀이 없는 사람 순위. 팀원과 운영자만 볼 수 있다. */
export function fetchTeamCandidates(teamId: number): Promise<{ candidates: TeamCandidate[] }> {
  return request(`/teams/${teamId}/candidates/`);
}


// ---------- 정규 기술 스택 목록 ----------

/** 프로필의 스택 선택 목록. 백엔드가 정본이라 프론트에 같은 목록을 두지 않는다. */
export function fetchTechStacks(): Promise<{ stacks: TechStack[] }> {
  return request('/tech-stacks/');
}


// ---------- 심사 보조 (제출 저장소 사전 분석) ----------

/** 이 제출물의 분석 결과 전부. 운영자·배정된 심사위원만 부를 수 있다(참가자는 403). */
export function fetchSubmissionReviews(
  submissionId: number
): Promise<{ reviews: SubmissionReview[] }> {
  return request(`/submissions/${submissionId}/reviews/`);
}

/**
 * 제출물 하나를 지정한 모델로 분석한다 (운영자 전용).
 *
 * 응답은 완료된 결과가 아니라 **'분석 중' 행**이다 — 서버가 백그라운드에서 돌린다. 워커가
 * 1개라 요청 안에서 LLM 을 기다리면 그동안 서비스 전체가 멈추기 때문이다. 완료는 폴링으로
 * 확인한다.
 */
export function analyzeSubmission(
  submissionId: number,
  provider?: string,
  model?: string
): Promise<SubmissionReview> {
  return request(`/submissions/${submissionId}/analyze/`, {
    method: 'POST',
    body: JSON.stringify({ provider, model }),
  });
}

/** 대회의 제출물 전체를 한 모델로 분석한다 (운영자 전용, 심사 전에 한 번). */
export function analyzeContestSubmissions(
  slug: string,
  provider?: string,
  model?: string
): Promise<{ queued: number; provider: string; model: string; reviews: SubmissionReview[] }> {
  return request(`/contests/${slug}/analyze_submissions/`, {
    method: 'POST',
    body: JSON.stringify({ provider, model }),
  });
}
