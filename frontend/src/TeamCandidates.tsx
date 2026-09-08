import { useState } from 'react';
import { fetchTeamCandidates } from './api';
import type { TeamCandidate } from './types';

const ROLE_LABEL: Record<string, string> = {
  frontend: '프론트엔드',
  backend: '백엔드',
  mobile: '모바일',
  design: '디자인',
  data: '데이터',
  planning: '기획',
  devops: '인프라',
};

interface TeamCandidatesProps {
  teamId: number;
}

/**
 * 이 팀에 맞는, 아직 팀이 없는 사람 순위 (팀원·운영자만).
 *
 * 접힌 채로 시작하고 눌러야 부른다 — 팀 목록은 5초마다 폴링되는데 카드마다 후보를 자동으로
 * 받아오면 팀 수만큼 요청이 늘어난다. 여기서 하는 일은 **소개까지만**이다: 데려오는 것은
 * 팀이 직접 연락해서 하고, 상대가 참가 버튼을 누른다.
 */
export function TeamCandidates({ teamId }: TeamCandidatesProps) {
  const [candidates, setCandidates] = useState<TeamCandidate[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function load() {
    setError('');
    setBusy(true);
    try {
      const { candidates: list } = await fetchTeamCandidates(teamId);
      setCandidates(list);
    } catch (err) {
      setError(err instanceof Error ? err.message : '후보를 불러오지 못했습니다');
    } finally {
      setBusy(false);
    }
  }

  if (candidates === null) {
    return (
      <div className="candidate-panel">
        <button type="button" className="candidate-toggle" onClick={load} disabled={busy}>
          {busy ? '찾는 중…' : '팀에 맞는 사람 찾기'}
        </button>
        {error && <p className="form-error">{error}</p>}
      </div>
    );
  }

  return (
    <div className="candidate-panel">
      <div className="candidate-head">
        <strong>팀에 맞는 사람</strong>
        <button type="button" onClick={load} disabled={busy}>
          다시 찾기
        </button>
      </div>
      {candidates.length === 0 ? (
        <p className="empty-hint">지금은 팀을 찾는 사람이 없습니다.</p>
      ) : (
        <ul className="candidate-list">
          {candidates.map((c) => (
            <li key={c.username} className="candidate-row">
              <span className="candidate-score">{Math.round(c.score)}</span>
              <span className="candidate-main">
                <strong>{c.username}</strong>
                <span className="candidate-tags">
                  {[
                    c.roles.map((r) => ROLE_LABEL[r] ?? r).join(', '),
                    c.skills.join(', '),
                  ]
                    .filter(Boolean)
                    .join(' · ') || '태그 없음'}
                </span>
                <span className="candidate-reasons">{c.reasons.join(' · ')}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
      <p className="empty-hint">
        연락은 직접 하세요 — 참가는 본인이 눌러야 합니다.
      </p>
    </div>
  );
}
