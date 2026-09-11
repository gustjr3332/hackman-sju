import { useEffect, useState } from 'react';
import { analyzeContestSubmissions, fetchLlmProviders } from './api';
import { SubmissionAnalysisPanel } from './SubmissionAnalysis';
import type { LlmProvider, Team } from './types';

interface JudgeAssistPanelProps {
  contestSlug: string;
  teams: Team[];
}

/**
 * 운영자용 심사 보조 분석 실행·확인 화면.
 *
 * **심사 전에 한 번 돌리는 도구다.** 대회 당일 심사 중에 부르지 않는다 — gunicorn 워커가
 * 1개라 LLM 호출이 요청 안에서 도는 순간 스코어보드 폴링까지 전부 멈춘다. 그래서 서버는
 * 분석을 백그라운드로 돌리고 여기서는 상태를 폴링해서 본다.
 *
 * 심사위원은 이 화면을 쓰지 않는다. 모델을 고르는 것도 운영자이고(분석은 심사 전에 이미
 * 끝나 있다), 심사위원은 심사 도구에서 저장된 결과를 보기만 한다.
 */
export function JudgeAssistPanel({ contestSlug, teams }: JudgeAssistPanelProps) {
  const [providers, setProviders] = useState<LlmProvider[]>([]);
  const [provider, setProvider] = useState('');
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState(false);
  const [openTeamId, setOpenTeamId] = useState<number | null>(null);

  useEffect(() => {
    fetchLlmProviders()
      .then(({ providers: list }) => {
        setProviders(list);
        if (list.length) setProvider(list[0].provider);
      })
      .catch(() => setProviders([]));
  }, []);

  // 저장소 주소가 없는 제출물은 분석할 것이 없다.
  const analyzable = teams.filter((t) => t.submission?.repo_url);

  async function runAll() {
    setBusy(true);
    setStatus('');
    try {
      const chosen = providers.find((p) => p.provider === provider);
      const res = await analyzeContestSubmissions(
        contestSlug,
        chosen?.provider,
        chosen?.default_model
      );
      setStatus(
        `${res.queued}개 제출물을 ${res.model} 로 분석 대기에 넣었습니다 — 순차로 돌아 몇 분 걸립니다.`
      );
    } catch (err) {
      setStatus(err instanceof Error ? err.message : '분석을 시작하지 못했습니다');
    } finally {
      setBusy(false);
    }
  }

  if (providers.length === 0) {
    return (
      <p className="empty-hint">
        LLM API 키가 설정되어 있지 않아 분석을 실행할 수 없습니다. 키를 넣으면 이 화면이 열립니다.
      </p>
    );
  }

  return (
    <div className="assist-panel">
      <div className="assist-controls">
        {providers.length > 1 && (
          <select
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
            aria-label="분석에 쓸 모델"
          >
            {providers.map((p) => (
              <option key={p.provider} value={p.provider}>
                {p.label} · {p.default_model}
              </option>
            ))}
          </select>
        )}
        <button type="button" onClick={runAll} disabled={busy || analyzable.length === 0}>
          {busy ? '시작하는 중…' : `전체 분석 실행 (${analyzable.length}팀)`}
        </button>
        <span className="empty-hint">
          심사 전에 한 번 실행합니다. 같은 팀을 다른 모델로 다시 돌려 나란히 비교할 수 있습니다.
        </span>
      </div>

      {status && <p className="empty-hint">{status}</p>}

      {analyzable.length === 0 && (
        <p className="empty-hint">GitHub 저장소를 등록한 제출물이 아직 없습니다.</p>
      )}

      <ul className="assist-list">
        {analyzable.map((team) => {
          const open = openTeamId === team.id;
          return (
            <li key={team.id}>
              <button
                type="button"
                className="assist-row"
                aria-expanded={open}
                onClick={() => setOpenTeamId(open ? null : team.id)}
              >
                <span>{team.name}</span>
                <span className="submission-summary">{team.submission!.title}</span>
              </button>
              {open && (
                <SubmissionAnalysisPanel
                  submissionId={team.submission!.id}
                  canRun
                  providers={providers}
                />
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
