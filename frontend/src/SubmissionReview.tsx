import { useEffect, useState } from 'react';
import {
  fetchDefaultBranch,
  fetchFileContent,
  fetchReadme,
  fetchTree,
  GithubApiError,
  parseGithubRepo,
  type GithubFile,
} from './github';
import type { Submission } from './types';

const GITHUB_ERROR_LABEL: Record<string, string> = {
  'not-found': '비공개 저장소이거나 찾을 수 없습니다',
  'rate-limit': 'GitHub API 사용량을 초과했습니다',
  error: '코드를 불러오지 못했습니다',
};

/** 심사위원이 필요할 때만 펼쳐 보는 데모/코드 열람 패널. 펼치기 전에는 아무 요청도 하지 않는다. */
export function SubmissionReviewPanel({ submission }: { submission: Submission }) {
  const [open, setOpen] = useState(false);

  if (!submission.link_url && !submission.repo_url) return null;

  return (
    <div className="review-panel">
      <button
        type="button"
        className="review-toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? '심사 도구 닫기' : '심사 도구 열기 (데모 · 코드)'}
        {/* 딩벳 문자 대신 SVG (DESIGN.md "아이콘 · 모션") */}
        <svg
          className={`chevron${open ? ' open' : ''}`}
          width="14"
          height="14"
          viewBox="0 0 16 16"
          fill="none"
          aria-hidden="true"
        >
          <polyline
            points="4,6 8,10 12,6"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>
      {open && (
        <div className="review-body">
          {submission.link_url && <DemoPanel linkUrl={submission.link_url} />}
          {submission.repo_url && <GithubPanel repoUrl={submission.repo_url} />}
        </div>
      )}
    </div>
  );
}

function DemoPanel({ linkUrl }: { linkUrl: string }) {
  return (
    <div className="demo-panel">
      <div className="review-panel-head">
        <h5>웹 데모</h5>
        <a href={linkUrl} target="_blank" rel="noreferrer">
          새 탭에서 열기 ↗
        </a>
      </div>
      {/* X-Frame-Options로 iframe이 막히는지는 JS로 감지할 수 없어, "새 탭에서 열기"를
          fallback이 아니라 항상 함께 노출한다 (DEVELOPMENT.md 참고). */}
      <iframe
        src={linkUrl}
        title="제출물 데모"
        className="demo-frame"
        sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-modals allow-popups-to-escape-sandbox"
      />
    </div>
  );
}

type GithubState =
  | { status: 'loading' }
  | { status: 'error'; kind: string }
  | { status: 'ready'; readme: string | null; files: GithubFile[]; branch: string; truncated: boolean };

function GithubPanel({ repoUrl }: { repoUrl: string }) {
  const ref = parseGithubRepo(repoUrl);
  const [state, setState] = useState<GithubState>({ status: 'loading' });
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<string | null>(null);
  const [fileError, setFileError] = useState('');

  useEffect(() => {
    if (!ref) return;
    let cancelled = false;
    setState({ status: 'loading' });
    setSelectedPath(null);
    setFileContent(null);
    (async () => {
      try {
        const branch = await fetchDefaultBranch(ref);
        const [readme, tree] = await Promise.all([
          fetchReadme(ref).catch(() => null),
          fetchTree(ref, branch),
        ]);
        if (cancelled) return;
        setState({ status: 'ready', readme, files: tree.files, branch, truncated: tree.truncated });
      } catch (err) {
        if (cancelled) return;
        const kind = err instanceof GithubApiError ? err.kind : 'error';
        setState({ status: 'error', kind });
      }
    })();
    return () => {
      cancelled = true;
    };
    // repoUrl이 바뀔 때만 다시 부른다 (ref는 repoUrl에서 파생).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repoUrl]);

  async function handleSelectFile(path: string) {
    if (!ref) return;
    setSelectedPath(path);
    setFileContent(null);
    setFileError('');
    try {
      const content = await fetchFileContent(ref, path);
      setFileContent(content);
    } catch {
      setFileError('파일을 불러오지 못했습니다.');
    }
  }

  const openInGithub = (
    <a href={repoUrl} target="_blank" rel="noreferrer">
      GitHub에서 직접 열기 ↗
    </a>
  );

  if (!ref) {
    return (
      <div className="github-panel">
        <div className="review-panel-head">
          <h5>GitHub 코드</h5>
        </div>
        <p className="form-error">GitHub 저장소 URL 형식이 아닙니다. {openInGithub}</p>
      </div>
    );
  }

  return (
    <div className="github-panel">
      <div className="review-panel-head">
        <h5>GitHub 코드</h5>
        {openInGithub}
      </div>

      {state.status === 'loading' && <p className="empty-hint">불러오는 중...</p>}

      {state.status === 'error' && (
        <p className="form-error">
          {GITHUB_ERROR_LABEL[state.kind] ?? GITHUB_ERROR_LABEL.error}. {openInGithub}
        </p>
      )}

      {state.status === 'ready' && (
        <div className="github-panel-body">
          <div className="github-tree">
            <p className="github-meta">
              브랜치 {state.branch} · 파일 {state.files.length}개
              {state.truncated && ' (일부만 표시)'}
            </p>
            <ul>
              {state.files.map((f) => (
                <li key={f.path}>
                  <button
                    type="button"
                    className={selectedPath === f.path ? 'active' : ''}
                    onClick={() => handleSelectFile(f.path)}
                  >
                    {f.path}
                  </button>
                </li>
              ))}
              {state.files.length === 0 && <li className="empty-hint">파일이 없습니다.</li>}
            </ul>
          </div>
          <div className="github-content">
            {!selectedPath && state.readme && (
              <>
                <p className="github-meta">README</p>
                <pre>{state.readme}</pre>
              </>
            )}
            {!selectedPath && !state.readme && (
              <p className="empty-hint">왼쪽에서 파일을 선택하면 내용을 볼 수 있습니다.</p>
            )}
            {selectedPath && (
              <>
                <p className="github-meta">{selectedPath}</p>
                {fileError && (
                  <p className="form-error">
                    {fileError} {openInGithub}
                  </p>
                )}
                {!fileError && fileContent == null && <p className="empty-hint">불러오는 중...</p>}
                {!fileError && fileContent != null && <pre>{fileContent}</pre>}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
