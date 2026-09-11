import { useCallback, useEffect, useRef, useState } from 'react';
import { analyzeSubmission, fetchSubmissionReviews } from './api';
import type { LlmProvider, SubmissionReview } from './types';

const KIND_LABEL: Record<string, string> = {
  implemented: '구현됨',
  shell: '껍데기',
  note: '참고',
};

const STATUS_LABEL: Record<string, string> = {
  pending: '분석 중…',
  done: '분석 완료',
  failed: '분석 실패',
};

/** 분석이 도는 동안만 도는 폴링 주기. 스코어보드(5초)보다 느리게 둔다 — 분석은 분 단위다. */
const POLL_MS = 6000;

interface AnalysisPanelProps {
  submissionId: number;
  /** 운영자만 실행 버튼을 본다. 심사위원은 저장된 결과를 보기만 한다. */
  canRun?: boolean;
  /** 실행에 쓸 제공사 목록. 비어 있으면 실행 UI 를 숨긴다(키가 하나도 없는 경우). */
  providers?: LlmProvider[];
}

/**
 * 제출 저장소 사전 분석 패널.
 *
 * 심사위원은 팀당 10분 안에 저장소를 다 읽을 수 없다. 여기 있는 것은 그 읽기를 미리 해 둔
 * 결과이고, **점수는 제안하지 않는다** — 제안 점수를 띄우면 심사위원이 거기에 닻을 내려
 * 결국 모델이 채점하는 것과 같아진다. 그래서 이 화면도 점수처럼 읽히는 요약(별점·등급·
 * 종합 판정)을 만들지 않고, 항목마다 **근거 파일 경로**를 붙여 심사위원이 직접 열어 볼 수
 * 있게 한다.
 */
export function SubmissionAnalysisPanel({
  submissionId,
  canRun = false,
  providers = [],
}: AnalysisPanelProps) {
  const [reviews, setReviews] = useState<SubmissionReview[] | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [provider, setProvider] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  // 폴링 타이머가 언마운트 뒤에도 살아 있지 않게 한다.
  const alive = useRef(true);

  const load = useCallback(async () => {
    try {
      const { reviews: list } = await fetchSubmissionReviews(submissionId);
      if (!alive.current) return;
      setReviews(list);
      setSelected((prev) => (prev != null && list.some((r) => r.id === prev) ? prev : list[0]?.id ?? null));
    } catch (err) {
      if (!alive.current) return;
      // 참가자는 403 이다 — 이 패널 자체가 심사위원·운영자 화면에만 붙지만, 권한이 바뀌는
      // 경우까지 조용히 넘긴다(분석이 없다고 심사가 막히면 안 된다).
      setReviews([]);
      setError(err instanceof Error ? err.message : '분석 결과를 불러오지 못했습니다');
    }
  }, [submissionId]);

  useEffect(() => {
    alive.current = true;
    load();
    return () => {
      alive.current = false;
    };
  }, [load]);

  // 분석 중인 행이 하나라도 있는 동안만 다시 부른다. 끝나면 폴링도 멈춘다.
  const pending = (reviews ?? []).some((r) => r.status === 'pending');
  useEffect(() => {
    if (!pending) return;
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [pending, load]);

  useEffect(() => {
    if (providers.length && !provider) setProvider(providers[0].provider);
  }, [providers, provider]);

  async function run() {
    setBusy(true);
    setError('');
    try {
      const chosen = providers.find((p) => p.provider === provider);
      await analyzeSubmission(submissionId, chosen?.provider, chosen?.default_model);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : '분석을 시작하지 못했습니다');
    } finally {
      setBusy(false);
    }
  }

  if (reviews == null) return <p className="empty-hint">분석 결과를 불러오는 중…</p>;

  const current = reviews.find((r) => r.id === selected) ?? null;

  return (
    <div className="analysis-panel">
      <div className="review-panel-head">
        <h5>저장소 사전 분석</h5>
        {canRun && providers.length > 0 && (
          <span className="analysis-run">
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
            <button type="button" onClick={run} disabled={busy}>
              {busy ? '시작하는 중…' : reviews.length ? '다시 분석' : '분석 실행'}
            </button>
          </span>
        )}
      </div>

      {error && <p className="form-error">{error}</p>}

      {reviews.length === 0 && (
        <p className="empty-hint">
          아직 분석이 없습니다. {canRun ? '' : '운영자가 심사 전에 실행합니다.'}
        </p>
      )}

      {/* 모델이 여럿이면 탭으로 나란히 비교한다 — 같은 저장소를 여러 모델로 돌려 보는 것이
          이 기능의 목적 중 하나다. */}
      {reviews.length > 1 && (
        <div className="analysis-tabs">
          {reviews.map((r) => (
            <button
              key={r.id}
              type="button"
              className={r.id === selected ? 'active' : ''}
              onClick={() => setSelected(r.id)}
            >
              {r.provider_label} · {r.model}
            </button>
          ))}
        </div>
      )}

      {current && <AnalysisResult review={current} />}
    </div>
  );
}

function AnalysisResult({ review }: { review: SubmissionReview }) {
  return (
    <div className="analysis-result">
      <p className="analysis-meta">
        <span className={`analysis-status ${review.status}`}>{STATUS_LABEL[review.status]}</span>
        {' · '}
        {review.provider_label} {review.model}
        {review.status === 'done' && ` · 파일 ${review.files_read}개 읽음`}
        {review.status === 'done' &&
          ` · 토큰 ${review.input_tokens.toLocaleString()}/${review.output_tokens.toLocaleString()}`}
      </p>

      {review.status === 'failed' && <p className="form-error">{review.error}</p>}

      {review.is_stale && (
        <p className="analysis-warning">
          이 분석 이후 제출물이 수정되었습니다 — 지금 저장소와 다를 수 있습니다.
        </p>
      )}
      {review.truncated && (
        <p className="analysis-warning">
          저장소가 커서 일부 파일만 읽었습니다. 아래 내용은 읽은 범위에 한정됩니다.
        </p>
      )}

      {review.summary && <p className="analysis-summary">{review.summary}</p>}

      {review.stack.length > 0 && (
        <p className="analysis-stack">
          {review.stack.map((s) => (
            <span key={s} className="stack-chip">
              {s}
            </span>
          ))}
        </p>
      )}

      {review.findings.length > 0 && (
        <ul className="analysis-findings">
          {review.findings.map((f, i) => (
            <li key={`${f.title}-${i}`} className={`finding ${f.kind}`}>
              <span className={`finding-kind ${f.kind}`}>{KIND_LABEL[f.kind]}</span>
              <span className="finding-title">{f.title}</span>
              <span className="finding-detail">{f.detail}</span>
              {f.paths.length > 0 && (
                <span className="finding-paths">
                  {f.paths.map((path) => (
                    <code key={path}>{path}</code>
                  ))}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}

      {review.status === 'done' && (
        <p className="empty-hint">
          이 분석은 점수를 매기지 않습니다. 위 경로를 직접 열어 확인한 뒤 판단하세요.
        </p>
      )}
    </div>
  );
}
