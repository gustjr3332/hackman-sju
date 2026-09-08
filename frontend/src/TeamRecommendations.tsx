import { useCallback, useEffect, useState } from 'react';
import { fetchRecommendedTeams, joinTeam } from './api';
import type { TeamRecommendation } from './types';

interface TeamRecommendationsProps {
  contestSlug: string;
  /** 팀에 들어간 뒤 상위 화면이 팀 목록을 다시 받도록. */
  onJoined: () => void;
}

/**
 * 나에게 맞는 팀 추천.
 *
 * 순위는 서버가 규칙으로 계산한다 — 모델이 팀을 정해주지 않는다. **제안까지만 하고 선택은
 * 사람이 한다**: 왜 이 순서인지(`reasons`)를 함께 보여주고, 들어가는 것은 참가자가 직접 누른다.
 * 이미 팀이 있거나 추천할 팀이 없으면 아무것도 그리지 않는다.
 */
export function TeamRecommendations({ contestSlug, onJoined }: TeamRecommendationsProps) {
  const [teams, setTeams] = useState<TeamRecommendation[]>([]);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState('');

  const load = useCallback(() => {
    fetchRecommendedTeams(contestSlug)
      .then(({ teams: list }) => setTeams(list))
      .catch(() => setTeams([]));
  }, [contestSlug]);

  useEffect(load, [load]);

  async function handleJoin(teamId: number) {
    setError('');
    setBusyId(teamId);
    try {
      await joinTeam(teamId);
      onJoined();
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : '팀 참가에 실패했습니다');
    } finally {
      setBusyId(null);
    }
  }

  if (teams.length === 0) return null;

  return (
    <div className="recommend-panel">
      <h3 className="section-heading">나에게 맞는 팀</h3>
      <p className="empty-hint">
        프로필을 기준으로 자리가 남은 팀을 추천합니다. 선택은 직접 하세요.
      </p>
      <ol className="recommend-list">
        {teams.map((team) => (
          <li key={team.team_id} className="recommend-row">
            <span className="recommend-score" title="적합도 (0~100)">
              {Math.round(team.score)}
            </span>
            <span className="recommend-main">
              <strong>{team.team_name}</strong>
              <span className="recommend-reasons">
                {team.reasons.length ? team.reasons.join(' · ') : `${team.member_count}명`}
              </span>
            </span>
            <button
              type="button"
              disabled={busyId === team.team_id}
              onClick={() => handleJoin(team.team_id)}
            >
              참가하기
            </button>
          </li>
        ))}
      </ol>
      {error && <p className="form-error">{error}</p>}
    </div>
  );
}
