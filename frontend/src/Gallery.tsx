import { useEffect, useState } from 'react';
import { fetchScoreboard, fetchTeams } from './api';
import { ROUND_LABEL, ROUNDS } from './labels';
import { navigate, paths } from './router';
import type { Contest, ScoreboardEntry, ScoreRound, Team } from './types';

interface GalleryProps {
  contest: Contest;
  isOrganizer: boolean;
}

/**
 * 제출물 갤러리. 로그인 없이 공개하고, 점수 공개 범위는 스코어보드와 같다(결선은 운영자·배정
 * 심사위원만). 서버가 이미 결선을 걸러 보내므로 여기서는 탭만 숨긴다.
 */
export function Gallery({ contest, isOrganizer }: GalleryProps) {
  const [teams, setTeams] = useState<Team[] | null>(null);
  const [board, setBoard] = useState<ScoreboardEntry[]>([]);
  const [round, setRound] = useState<ScoreRound>('preliminary');
  const [error, setError] = useState('');

  useEffect(() => {
    Promise.all([fetchTeams(contest.slug), fetchScoreboard(contest.slug)])
      .then(([t, b]) => {
        setTeams(t);
        setBoard(b);
      })
      .catch((err: Error) => setError(err.message));
  }, [contest.slug]);

  const canSeeFinal = contest.is_judge || isOrganizer;
  const rounds = canSeeFinal ? ROUNDS : ROUNDS.filter((r) => r !== 'final');
  // 스코어보드 응답은 이미 순위순(점수 없는 팀은 뒤)이라 그 순서를 그대로 쓴다.
  const entries = board.filter((e) => e.round === round);
  const byId = new Map((teams ?? []).map((t) => [t.id, t]));
  const ordered = entries.map((e) => ({ entry: e, team: byId.get(e.team_id) })).filter((x) => x.team);

  return (
    <section className="gallery">
      <button className="back-btn" type="button" onClick={() => navigate(paths.contest(contest.slug))}>
        ← {contest.name}
      </button>
      <div className="gallery-head">
        <h2>제출물 둘러보기</h2>
        <div className="line-tabs" role="tablist" aria-label="라운드">
          {rounds.map((r) => (
            <button
              key={r}
              type="button"
              role="tab"
              aria-selected={round === r}
              className={round === r ? 'active' : ''}
              onClick={() => setRound(r)}
            >
              {ROUND_LABEL[r]}
            </button>
          ))}
        </div>
      </div>

      {error && <p className="form-error">{error}</p>}
      {teams === null && !error && <p className="empty-hint">불러오는 중…</p>}
      {teams !== null && ordered.length === 0 && <p className="empty-hint">아직 등록된 팀이 없습니다.</p>}

      <div className="tile-grid">
        {ordered.map(({ entry, team }) => {
          const sub = team!.submission;
          const scored = entry.average_score !== null;
          const open = () => sub && navigate(paths.project(contest.slug, team!.id));
          return (
            <article
              key={team!.id}
              className={`tile${scored ? ' scored' : ''}${sub ? '' : ' unsubmitted'}`}
              onClick={open}
              tabIndex={sub ? 0 : -1}
              onKeyDown={(e) => e.key === 'Enter' && open()}
            >
              <div className="tile-top">
                <h3>{team!.name}</h3>
                <span className="tile-score">
                  {sub ? (scored ? Number(entry.average_score).toFixed(2) : '–') : '미제출'}
                </span>
              </div>
              <p className="tile-title">{sub ? sub.title : '아직 제출물이 없습니다'}</p>
              {sub?.description && <p className="tile-desc">{sub.description}</p>}
              {sub && (
                <p className="tile-links">
                  {sub.link_url && <span>데모</span>}
                  {sub.repo_url && <span>코드</span>}
                </p>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}
