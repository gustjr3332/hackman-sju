export type ContestStatus = 'recruiting' | 'ongoing' | 'judging' | 'closed';

export interface Contest {
  slug: string;
  name: string;
  description: string;
  status: ContestStatus;
  start_at: string;
  end_at: string;
  created_at: string;
  updated_at: string;
  team_count: number;
  /** 요청한 사용자가 이 대회의 심사위원인지 (서버 판단, 폴링으로 갱신). */
  is_judge: boolean;
  /** 팀이 따로 정하지 않았을 때 쓰는 기본 발표 시간(분). */
  presentation_minutes: number;
}

export interface ContestInput {
  slug: string;
  name: string;
  description: string;
  start_at: string;
  end_at: string;
}

export interface Participant {
  id: number;
  team: number;
  username: string;
  joined_at: string;
}

export interface Submission {
  id: number;
  team: number;
  title: string;
  description: string;
  link_url: string;
  repo_url: string;
  submitted_at: string;
}

export interface Team {
  id: number;
  contest: string;
  name: string;
  created_at: string;
  participants: Participant[];
  submission: Submission | null;
  /** 발표 순서(1부터). 아직 배정 전이면 null. 운영자만 바꿀 수 있다. */
  presentation_order: number | null;
  /** 이 팀만의 발표 시간(분, 1~30). null 이면 대회 기본값을 따른다. */
  presentation_minutes: number | null;
  /** 실제로 적용되는 발표 시간(분) — 위 값이 null 이면 대회 기본값. */
  effective_presentation_minutes: number;
  /** 운영자가 "발표 시작"을 누른 실제 시각. 누르기 전에는 null. */
  presentation_started_at: string | null;
  /** 발표가 끝난 시각. 진행 중이면 null. */
  presentation_ended_at: string | null;
  /** 발표 중일 때만 채워지는 종료 예정 시각 — 타이머는 이 값이 있을 때만 돈다. */
  presentation_due_at: string | null;
}

export type ScoreRound = 'preliminary' | 'final';

export interface Judge {
  id: number;
  contest: string;
  username: string;
  /** 이 심사위원이 입력한 점수 수. 0 보다 크면 해제할 수 없다. */
  score_count: number;
}

export interface Me {
  username: string;
  is_staff: boolean;
}

export interface Score {
  id: number;
  submission: number;
  judge: number;
  judge_username: string;
  round: ScoreRound;
  value: string;
  comment: string;
  created_at: string;
  updated_at: string;
}

export interface ScoreboardEntry {
  team_id: number;
  team_name: string;
  submission_title: string | null;
  round: ScoreRound;
  average_score: string | null;
  vote_count: number;
  /** 라운드 내 순위. 점수가 없는 팀은 null. 동점은 같은 순위. */
  rank: number | null;
}

export interface AuthTokens {
  access: string;
  refresh: string;
}

/** rank 1이 최상위 상. 시상식 전까지는 운영자만 볼 수 있다. */
export interface Award {
  id: number;
  contest: string;
  rank: number;
  title: string;
}


/** 팀빌딩 프로필. 계정에 하나만 있고, 대회를 옮겨 다녀도 그대로 쓴다. */
export interface Profile {
  username: string;
  /** 참가자가 직접 쓴 자유 서술. 이것이 원본이고 나머지 태그는 여기서 파생된다. */
  intro: string;
  /** 개인 GitHub 주소. 태그만으로 안 보이는 실제 결과물을 팀이 직접 확인하는 통로. */
  github_url: string;
  /** 정규 목록(`TechStack.slug`)에서 고른 것만 들어간다. 서버가 목록 밖 값을 거절한다. */
  skills: string[];
  /** 자동 정리가 목록에 매핑하지 못한 원문. 읽기 전용 — 버리지 않고 보여주기만 한다. */
  other_skills: string[];
  interests: string[];
  roles: string[];
  level: '' | 'beginner' | 'intermediate' | 'advanced';
  looking_for_team: boolean;
  extraction_status: 'empty' | 'pending' | 'done' | 'failed';
  extraction_error: string;
  extracted_by: string;
  extracted_at: string | null;
  updated_at: string;
}

/** 정규 기술 스택 한 건. 목록은 백엔드가 정본이고(`/api/tech-stacks/`) 화면은 받아 쓰기만 한다. */
export interface TechStack {
  slug: string;
  name: string;
  category: 'language' | 'framework' | 'tool';
  /** 표기 흔들림과 한국어 통용 표기. 검색이 `파이썬`으로 `Python` 을 찾게 하는 데 쓴다. */
  aliases: string[];
}

/** 심사 보조 분석의 항목 하나. 근거 파일 경로가 붙지 않는 주장은 만들지 않는 것이 전제다. */
export interface ReviewFinding {
  /** implemented = 동작하는 코드가 있음, shell = 껍데기만 있음, note = 알아둘 사실 */
  kind: 'implemented' | 'shell' | 'note';
  title: string;
  detail: string;
  paths: string[];
}

/**
 * 제출 저장소 사전 분석 1건. 운영자·배정된 심사위원만 볼 수 있다.
 *
 * **점수 필드가 없다** — 제안 점수를 띄우면 심사위원이 거기 닻을 내려 결국 모델이 채점하는
 * 것과 같아진다. 화면도 점수처럼 보이는 요약(별점·등급 등)을 만들지 않는다.
 */
export interface SubmissionReview {
  id: number;
  submission: number;
  provider: string;
  provider_label: string;
  model: string;
  status: 'pending' | 'done' | 'failed';
  summary: string;
  findings: ReviewFinding[];
  stack: string[];
  cited_paths: string[];
  /** 저장소가 커서 일부만 읽었는지. 조용히 자르지 않고 화면에 표시한다. */
  truncated: boolean;
  files_read: number;
  input_tokens: number;
  output_tokens: number;
  error: string;
  /** 분석 이후 제출물이 바뀌었는지. 바뀌었으면 다시 돌려야 한다. */
  is_stale: boolean;
  created_at: string;
  updated_at: string;
}

/** 키가 설정된 LLM 제공사. 하나도 없으면 자동 정리 기능만 꺼지고 추천은 그대로 동작한다. */
export interface LlmProvider {
  provider: string;
  label: string;
  default_model: string;
}

/** 나에게 맞는 팀 한 건. score 는 0~100, reasons 는 왜 그 순위인지. */
export interface TeamRecommendation {
  team_id: number;
  team_name: string;
  member_count: number;
  score: number;
  reasons: string[];
}

/** 이 팀에 맞는 후보 한 명. */
export interface TeamCandidate {
  username: string;
  skills: string[];
  roles: string[];
  /** 본인이 적었을 때만 값이 있다. 팀이 실제 결과물을 확인하는 통로. */
  github_url: string;
  score: number;
  reasons: string[];
}
