import type { ContestStatus } from './types';

// 대회 상태별 허용 동작. 백엔드 `contests/views.py`의 *_STATUSES 와 동일하게 유지한다.
// 여기서 막는 것은 UI 안내용이고, 실제 강제는 서버가 한다.

// 팀 모집은 모집중에서만. 제출물(canSubmit)은 진행중에도 열려 있다 — 진행중이 곧 개발 시간이다.
export function canFormTeams(status: ContestStatus): boolean {
  return status === 'recruiting';
}

export function canSubmit(status: ContestStatus): boolean {
  return status === 'recruiting' || status === 'ongoing';
}

export function canScore(status: ContestStatus): boolean {
  return status === 'judging';
}

export function isLive(status: ContestStatus): boolean {
  return status !== 'closed';
}
