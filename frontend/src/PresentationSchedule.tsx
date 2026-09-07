import { useState } from 'react';
import {
  assignPresentationOrder,
  endPresentation,
  resetPresentation,
  startPresentation,
  updateTeamPresentation,
} from './api';
import { CountdownTimer } from './CountdownTimer';
import type { Contest, Team } from './types';

/** 팀별 발표 시간의 허용 범위(분). 서버 validator 와 같은 값을 쓴다. */
const MIN_MINUTES = 1;
const MAX_MINUTES = 30;

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('ko-KR', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

interface PresentationScheduleProps {
  contest: Contest;
  teams: Team[];
  isOrganizer: boolean;
  /** 서버 상태가 바뀌었으니 팀 목록을 다시 받으라는 신호. */
  onChanged: () => void;
}

/**
 * 발표 순서·시간표. 시계에 맞춘 예정표가 아니라 이벤트 기반이다 — 운영자가 팀마다
 * "발표 시작"을 눌러야 그 팀의 타이머가 돌고, 팀 교체·쉬는 시간에는 아무도 시작 상태가
 * 아니므로 타이머가 멈춘다. 앞 팀이 늦어져도 뒤 팀의 시간이 깎이지 않는다.
 */
export function PresentationSchedule({
  contest,
  teams,
  isOrganizer,
  onChanged,
}: PresentationScheduleProps) {
  const [busyId, setBusyId] = useState<number | null>(null);
  const [assigning, setAssigning] = useState(false);
  const [error, setError] = useState('');

  const scheduled = teams
    .filter((t) => t.presentation_order != null)
    .sort((a, b) => (a.presentation_order ?? 0) - (b.presentation_order ?? 0));

  const running = scheduled.find(
    (t) => t.presentation_started_at != null && t.presentation_ended_at == null
  );

  async function run(teamId: number | null, action: () => Promise<unknown>) {
    setError('');
    setBusyId(teamId);
    try {
      await action();
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : '요청에 실패했습니다');
    } finally {
      setBusyId(null);
    }
  }

  async function handleAssign() {
    setError('');
    setAssigning(true);
    try {
      await assignPresentationOrder(contest.slug);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : '발표 순서 배정에 실패했습니다');
    } finally {
      setAssigning(false);
    }
  }

  /** 두 팀의 순서 번호를 맞바꾼다. 드래그보다 손이 덜 가고 현장에서 실수가 적다. */
  async function swapWith(team: Team, neighbour: Team) {
    setError('');
    setBusyId(team.id);
    try {
      await updateTeamPresentation(team.id, {
        presentation_order: neighbour.presentation_order ?? undefined,
      });
      await updateTeamPresentation(neighbour.id, {
        presentation_order: team.presentation_order ?? undefined,
      });
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : '순서 변경에 실패했습니다');
    } finally {
      setBusyId(null);
    }
  }

  if (!isOrganizer && scheduled.length === 0) return null;

  return (
    <div>
      <h3 className="section-heading">발표 일정</h3>

      {isOrganizer && (
        <div className="presentation-assign-form">
          <button type="button" onClick={handleAssign} disabled={assigning}>
            {scheduled.length > 0 ? '제출 시각순으로 재배정' : '발표 순서 배정'}
          </button>
          <span className="empty-hint">
            제출이 빠른 팀부터 배정합니다. 그 뒤로는 아래에서 자유롭게 바꿀 수 있습니다.
          </span>
        </div>
      )}
      {error && <p className="form-error">{error}</p>}

      {running && running.presentation_due_at && (
        <CountdownTimer
          targetIso={running.presentation_due_at}
          label={`${running.name} 발표 종료까지`}
          expiredLabel={`${running.name} 발표 시간이 끝났습니다`}
        />
      )}

      {scheduled.length === 0 ? (
        <p className="empty-hint">아직 발표 순서가 배정되지 않았습니다.</p>
      ) : (
        <ol className="presentation-list">
          {scheduled.map((team, index) => {
            const isRunning = running?.id === team.id;
            const isDone = team.presentation_ended_at != null;
            const busy = busyId === team.id;
            return (
              <li
                key={team.id}
                className={`presentation-row${isRunning ? ' current' : ''}${isDone ? ' done' : ''}`}
              >
                <span className="presentation-order">{team.presentation_order}</span>
                <span className="presentation-team">{team.name}</span>

                <span className="presentation-time">
                  {team.presentation_started_at
                    ? `${formatTime(team.presentation_started_at)}${
                        team.presentation_ended_at
                          ? ` – ${formatTime(team.presentation_ended_at)}`
                          : ' –'
                      }`
                    : `${team.effective_presentation_minutes}분 예정`}
                </span>

                {isRunning && <span className="presentation-badge">발표 중</span>}

                {isOrganizer && (
                  <span className="presentation-controls">
                    <label className="presentation-minutes">
                      <input
                        type="number"
                        min={MIN_MINUTES}
                        max={MAX_MINUTES}
                        step={1}
                        value={team.presentation_minutes ?? ''}
                        placeholder={String(contest.presentation_minutes)}
                        aria-label={`${team.name} 발표 시간(분)`}
                        disabled={busy}
                        onChange={(e) => {
                          const raw = e.target.value;
                          // 비우면 대회 기본값으로 되돌린다(null).
                          const minutes = raw === '' ? null : Number(raw);
                          if (
                            minutes !== null &&
                            (minutes < MIN_MINUTES || minutes > MAX_MINUTES)
                          ) {
                            return;
                          }
                          run(team.id, () =>
                            updateTeamPresentation(team.id, { presentation_minutes: minutes })
                          );
                        }}
                      />
                      분
                    </label>

                    <button
                      type="button"
                      aria-label={`${team.name} 순서 올리기`}
                      disabled={busy || index === 0}
                      onClick={() => swapWith(team, scheduled[index - 1])}
                    >
                      ↑
                    </button>
                    <button
                      type="button"
                      aria-label={`${team.name} 순서 내리기`}
                      disabled={busy || index === scheduled.length - 1}
                      onClick={() => swapWith(team, scheduled[index + 1])}
                    >
                      ↓
                    </button>

                    {isRunning ? (
                      <button
                        type="button"
                        className="presentation-primary"
                        disabled={busy}
                        onClick={() => run(team.id, () => endPresentation(team.id))}
                      >
                        발표 종료
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="presentation-primary"
                        disabled={busy}
                        onClick={() => run(team.id, () => startPresentation(team.id))}
                      >
                        {isDone ? '다시 시작' : '발표 시작'}
                      </button>
                    )}

                    {(isRunning || isDone) && (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => run(team.id, () => resetPresentation(team.id))}
                      >
                        기록 지우기
                      </button>
                    )}
                  </span>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
