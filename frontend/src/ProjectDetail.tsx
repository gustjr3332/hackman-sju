import { useEffect, useState } from 'react';
import { fetchScoreboard, fetchTeams } from './api';
import { ROUND_LABEL } from './labels';
import { navigate, paths } from './router';
import { DemoPanel, GithubPanel } from './SubmissionReview';
import type { Contest, ScoreboardEntry, Team } from './types';

interface ProjectDetailProps {
  contest: Contest;
  teamId: number;
}

/**
 * 프로젝트 상세(공개). 데모·코드 열람은 심사 도구의 패널을 그대로 쓴다.
 * 심사 보조 분석은 붙이지 않는다 — 참가자에게는 비공개인 정보다.
 */
export function ProjectDetail({ contest, teamId }: ProjectDetailProps) {
  const [team, setTeam] = useState<Team | null | undefined>(undefined);
  const [board, setBoard] = useState<ScoreboardEntry[]>([]);
  const [error, setError] = useState('');

  useEffect(() => {
    Promise.all([fetchTeams(contest.slug), fetchScoreboard(contest.slug)])
      .then(([teams, b]) => {
        setTeam(teams.find((t) => t.id === teamId) ?? null);
        setBoard(b);
      })
      .catch((err: Error) => setError(err.message));
  }, [contest.slug, teamId]);

  const back = (
    <button className="back-btn" type="button" onClick={() => navigate(paths.gallery(contest.slug))}>
      ← 제출물 둘러보기
    </button>
  );

  if (error) return <section className="project">{back}<p className="form-error">{error}</p></section>;
  if (team === undefined) return <section className="project">{back}<p className="empty-hint">불러오는 중…</p></section>;
  const sub = team?.submission;
  if (!team || !sub) {
    return <section className="project">{back}<p className="empty-hint">제출물을 찾을 수 없습니다.</p></section>;
  }

  // 서버가 권한에 맞게 걸러 보낸 라운드만 들어 있다(비공개 결선은 오지 않는다).
  const scores = board.filter((e) => e.team_id === team.id && e.average_score !== null);

  return (
    <section className="project">
      {back}
      <header className="project-head">
        <div>
          <h2>
            {team.name} · {sub.title}
          </h2>
          <p className="project-meta">
            제출{' '}
            {new Date(sub.submitted_at).toLocaleString('ko-KR', {
              year: 'numeric',
              month: '2-digit',
              day: '2-digit',
              hour: '2-digit',
              minute: '2-digit',
              hour12: false,
            })}
          </p>
        </div>
        <div className="project-scores">
          {scores.map((e) => (
            <div key={e.round} className="project-score">
              <span>{Number(e.average_score).toFixed(2)}</span>
              <small>{ROUND_LABEL[e.round]} 평균 점수</small>
            </div>
          ))}
        </div>
      </header>

      <div className="project-body">
        <div className="project-main">
          <div>
            <h3 className="section-heading">소개</h3>
            <p className="project-desc">{sub.description || '소개가 없습니다.'}</p>
          </div>
          {sub.link_url && <DemoPanel linkUrl={sub.link_url} />}
          {sub.repo_url && <GithubPanel repoUrl={sub.repo_url} contestStartAt={contest.start_at} />}
        </div>

        <aside className="project-aside">
          <h3 className="section-heading">팀</h3>
          <dl>
            <dt>팀원</dt>
            <dd>{team.participants.map((p) => p.username).join(', ') || '없음'}</dd>
            {sub.repo_url && (
              <>
                <dt>리포지토리</dt>
                <dd>
                  <a href={sub.repo_url} target="_blank" rel="noreferrer">
                    {sub.repo_url.replace(/^https?:\/\//, '')}
                  </a>
                </dd>
              </>
            )}
            {sub.link_url && (
              <>
                <dt>데모 링크</dt>
                <dd>
                  <a href={sub.link_url} target="_blank" rel="noreferrer">
                    {sub.link_url.replace(/^https?:\/\//, '')}
                  </a>
                </dd>
              </>
            )}
          </dl>
        </aside>
      </div>
    </section>
  );
}
