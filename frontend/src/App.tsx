import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AUTH_EXPIRED_EVENT,
  fetchContests,
  fetchMe,
  getStoredUsername,
  logout,
  onPasswordRecovery,
  onStoredUsernameChange,
  storeUsername,
} from './api';
import { AuthPanel, NewPasswordPanel } from './AuthPanel';
import { ContestDetail } from './ContestDetail';
import { ContestForm } from './ContestForm';
import { Gallery } from './Gallery';
import { STATUS_LABEL, STATUS_ORDER } from './labels';
import { ProjectDetail } from './ProjectDetail';
import { navigate, paths, useRoute } from './router';
import { ThemeToggle } from './ThemeToggle';
import type { Contest, ContestStatus } from './types';

export default function App() {
  const [contests, setContests] = useState<Contest[]>([]);
  // 어떤 화면을 볼지는 주소창 경로가 정한다(링크 공유·뒤로 가기).
  const route = useRoute();
  const selectedSlug = route.name === 'list' ? null : route.slug;
  const [query, setQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<ContestStatus | 'all'>('all');
  const [status, setStatus] = useState('불러오는 중…');
  const [username, setUsername] = useState<string | null>(getStoredUsername());
  const [isOrganizer, setIsOrganizer] = useState(false);
  const [showCreateForm, setShowCreateForm] = useState(false);
  // 로그인 폼은 기본으로 접혀 있다. 대회는 로그인 없이도 다 둘러볼 수 있어서 헤더의
  // "로그인" 버튼을 눌렀을 때만 편다.
  const [showAuth, setShowAuth] = useState(false);
  const [recovering, setRecovering] = useState(false);
  const [offline, setOffline] = useState(!navigator.onLine);

  // 상세 화면은 항상 최신 목록의 대회 객체를 본다 (상태 전이 후에도 동기화 유지).
  const selected = contests.find((c) => c.slug === selectedSlug) ?? null;

  const loadContests = useCallback(() => {
    return fetchContests()
      .then((data) => {
        setContests(data);
        setStatus('');
      })
      .catch((err: Error) => setStatus(err.message));
  }, []);

  useEffect(() => {
    loadContests();
  }, [loadContests]);

  // 폰에서는 지하철·강의실 이동 중에 연결이 자주 끊긴다. 끊기면 알리고, 다시 붙으면 목록을
  // 새로 받는다(상세 화면은 ContestDetail 이 따로 다시 받는다).
  useEffect(() => {
    const goOffline = () => setOffline(true);
    const goOnline = () => {
      setOffline(false);
      loadContests();
    };
    window.addEventListener('offline', goOffline);
    window.addEventListener('online', goOnline);
    return () => {
      window.removeEventListener('offline', goOffline);
      window.removeEventListener('online', goOnline);
    };
  }, [loadContests]);

  // 재설정 메일의 링크로 돌아오면 새 비밀번호를 정하게 한다.
  useEffect(() => onPasswordRecovery(() => setRecovering(true)), []);

  useEffect(() => {
    const handleExpired = () => {
      setUsername(null);
      setStatus('로그인이 만료되었습니다. 다시 로그인해 주세요.');
      loadContests();
    };
    window.addEventListener(AUTH_EXPIRED_EVENT, handleExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, handleExpired);
  }, [loadContests]);

  // 다른 탭에서 로그인/로그아웃하면 이 탭도 같은 계정으로 맞춘다 (토큰은 이미 localStorage 로 공유됨).
  useEffect(
    () =>
      onStoredUsernameChange((stored) => {
        setUsername(stored);
        setStatus('');
        loadContests();
      }),
    [loadContests]
  );

  useEffect(() => {
    if (!username) {
      setIsOrganizer(false);
      setShowCreateForm(false);
      return;
    }
    let cancelled = false;
    fetchMe()
      .then((me) => {
        if (cancelled) return;
        setIsOrganizer(me.is_staff);
        // 로그인 폼에 친 문자열 대신 서버가 아는 정식 아이디를 쓴다. "내 팀" 판단 등이 모두
        // 이 값과 비교하므로, 앞뒤 공백처럼 어긋나면 내 팀·채점 패널이 조용히 숨겨진다.
        if (me.username !== username) {
          storeUsername(me.username);
          setUsername(me.username);
        }
      })
      .catch(() => {
        if (!cancelled) setIsOrganizer(false);
      });
    return () => {
      cancelled = true;
    };
  }, [username]);

  // 대회 목록의 is_judge 는 로그인한 사용자에 따라 다르므로 로그인/로그아웃 직후 다시 받는다.
  async function handleLogout() {
    await logout();
    setUsername(null);
    setStatus('');
    loadContests();
  }

  function handleLoggedIn(name: string) {
    setUsername(name);
    setStatus('');
    setShowAuth(false);
    loadContests();
  }

  // ContestDetail 의 폴링 콜백으로도 쓰이므로 참조가 안정적이어야 한다 (useCallback).
  // 내용이 같으면 이전 배열을 그대로 돌려 불필요한 재렌더를 막는다.
  const handleContestUpdated = useCallback((updated: Contest) => {
    setContests((prev) => {
      const current = prev.find((c) => c.slug === updated.slug);
      if (
        current &&
        current.updated_at === updated.updated_at &&
        current.team_count === updated.team_count &&
        current.is_judge === updated.is_judge
      ) {
        return prev;
      }
      return prev.map((c) => (c.slug === updated.slug ? updated : c));
    });
  }, []);

  function handleContestCreated(created: Contest) {
    setContests((prev) => [created, ...prev.filter((c) => c.slug !== created.slug)]);
    setShowCreateForm(false);
    navigate(paths.contest(created.slug));
    loadContests();
  }

  // 삭제된 대회는 상세 화면이 그릴 것이 없으므로 목록으로 되돌린다. 목록에서도 먼저 지워
  // 두는 이유는, 서버 재조회를 기다리는 동안 방금 지운 대회가 카드로 남아 보이기 때문이다.
  const handleContestDeleted = useCallback(
    (slug: string) => {
      navigate(paths.list());
      setContests((prev) => prev.filter((c) => c.slug !== slug));
      loadContests();
    },
    [loadContests]
  );

  // 좌상단 로고를 언제든 눌러 대회 목록(초기 화면)으로 돌아간다.
  function handleGoHome() {
    navigate(paths.list());
    setShowCreateForm(false);
  }

  // 좁은 화면에서는 브레드크럼 대신 "뒤로 + 제목" 앱 바를 쓴다(design/mobile-ui 시안).
  // 제목은 바로 밑 화면 제목(h2)과 겹치지 않는 쪽을 고른다: 상세는 h2 가 대회 이름이라
  // "대회 상세", 갤러리·프로젝트는 h2 가 화면 이름이라 어느 대회 안인지를 보여 준다.
  // 뒤로는 브라우저 이력이 아니라 화면 계층을 따른다 — 공유 링크로 바로 들어와도 갈 곳이 있다.
  const appBar = !selected
    ? null
    : route.name === 'gallery'
      ? { title: selected.name, back: paths.contest(selected.slug), backLabel: selected.name }
      : route.name === 'project'
        ? { title: selected.name, back: paths.gallery(selected.slug), backLabel: '제출물 둘러보기' }
        : { title: '대회 상세', back: paths.list(), backLabel: '대회 목록' };

  const needle = query.trim().toLowerCase();
  const visibleContests = contests.filter(
    (c) =>
      (statusFilter === 'all' || c.status === statusFilter) &&
      (!needle || c.name.toLowerCase().includes(needle))
  );

  return (
    <>
      <header className={`site-header${appBar ? ' has-back' : ''}`}>
        <div className="brand">
          {appBar && (
            <button
              type="button"
              className="app-back"
              onClick={() => navigate(appBar.back)}
              aria-label={`${appBar.backLabel}(으)로 돌아가기`}
            >
              <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
                <polyline
                  points="12.5,4 6.5,10 12.5,16"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
          )}
          {appBar && <span className="app-title">{appBar.title}</span>}
          <button
            type="button"
            className="brand-home"
            onClick={handleGoHome}
            aria-label="대회 목록으로 이동"
          >
            <span className="brand-mark" aria-hidden="true">
              H
            </span>
            <span className="brand-word">HACKMAN</span>
          </button>
          {selected && (
            <>
              <span className="crumb-sep" aria-hidden="true">
                /
              </span>
              <button type="button" className="crumb" onClick={() => navigate(paths.contest(selected.slug))}>
                {selected.name}
              </button>
              {route.name !== 'contest' && (
                <>
                  <span className="crumb-sep" aria-hidden="true">
                    /
                  </span>
                  <span className="crumb">제출물</span>
                </>
              )}
            </>
          )}
        </div>
        <div className="header-right">
          {!username && (
            <button type="button" className="btn-login" onClick={() => setShowAuth((v) => !v)}>
              {showAuth ? '닫기' : '로그인'}
            </button>
          )}
          <ThemeToggle />
          {username && (
            <div className="auth-status">
              {/* 이니셜 아바타 + 이름/역할 2줄 (docs/REFERENCE.md 헤더 규격). */}
              <span className="avatar" aria-hidden="true">
                {username.slice(0, 1).toUpperCase()}
              </span>
              <span className="auth-identity">
                <span className="auth-name">{username}</span>
                <span className="auth-role">{isOrganizer ? '운영자' : '참가자'}</span>
              </span>
              <button type="button" onClick={handleLogout}>
                로그아웃
              </button>
            </div>
          )}
          {username && (
            <AccountMenu username={username} isOrganizer={isOrganizer} onLogout={handleLogout} />
          )}
        </div>
      </header>

      {offline && (
        <p className="offline-banner" role="status">
          오프라인입니다. 연결되면 자동으로 다시 불러옵니다.
        </p>
      )}

      <main className="main-content">
        {recovering && (
          <NewPasswordPanel
            onDone={(name) => {
              setRecovering(false);
              handleLoggedIn(name);
            }}
          />
        )}

        {/* 대회 목록·상세·스코어보드는 로그인 없이 다 보인다. 로그인 폼은 헤더 버튼으로만 편다. */}
        {!username && showAuth && <AuthPanel onLoggedIn={handleLoggedIn} />}

        {selected && route.name === 'gallery' ? (
          <Gallery contest={selected} isOrganizer={isOrganizer} />
        ) : selected && route.name === 'project' ? (
          <ProjectDetail contest={selected} teamId={route.teamId} />
        ) : selected ? (
          <ContestDetail
            contest={selected}
            username={username}
            isOrganizer={isOrganizer}
            onBack={() => navigate(paths.list())}
            onContestUpdated={handleContestUpdated}
            onDeleted={handleContestDeleted}
          />
        ) : selectedSlug && !status ? (
          <p className="empty-hint">
            대회를 찾을 수 없습니다.{' '}
            <button type="button" className="link-btn" onClick={handleGoHome}>
              목록으로
            </button>
          </p>
        ) : selectedSlug ? null : (
          <>
            <div className="page-head">
              <div>
                <h1>대회</h1>
                <p className="tagline">학과·동아리 해커톤을 만들고 실시간으로 운영합니다</p>
              </div>
              {isOrganizer && !showCreateForm && (
                <div className="organizer-bar">
                  <button type="button" onClick={() => setShowCreateForm(true)}>
                    + 새 대회 만들기
                  </button>
                </div>
              )}
            </div>
            {isOrganizer && showCreateForm && (
              <ContestForm
                onCreated={handleContestCreated}
                onCancel={() => setShowCreateForm(false)}
              />
            )}

            <div className="list-tools">
              <input
                type="search"
                className="contest-search"
                placeholder="대회 이름으로 검색"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                aria-label="대회 이름으로 검색"
              />
              <div className="line-tabs" role="tablist" aria-label="상태">
                {(['all', ...STATUS_ORDER] as const).map((st) => (
                  <button
                    key={st}
                    type="button"
                    role="tab"
                    aria-selected={statusFilter === st}
                    className={statusFilter === st ? 'active' : ''}
                    onClick={() => setStatusFilter(st)}
                  >
                    {st === 'all' ? '전체' : STATUS_LABEL[st]}
                  </button>
                ))}
              </div>
            </div>

            <section className="contest-list">
              {visibleContests.map((contest) => (
                <article
                  key={contest.slug}
                  className={`contest-card status-${contest.status}`}
                  onClick={() => navigate(paths.contest(contest.slug))}
                  tabIndex={0}
                  onKeyDown={(e) => e.key === 'Enter' && navigate(paths.contest(contest.slug))}
                >
                  {/* 상태 점·라벨 → 대회명 → 한 줄 설명 → 헤어라인 → 모노 메타 (docs/REFERENCE.md 카드 규격). */}
                  <span className={`status-badge status-${contest.status}`}>
                    {STATUS_LABEL[contest.status]}
                  </span>
                  <h2>{contest.name}</h2>
                  <p className="contest-card-desc">{contest.description}</p>
                  <p className="contest-meta">
                    <span>
                      {contest.start_at.slice(0, 10)} – {contest.end_at.slice(0, 10)}
                    </span>
                    <span>{contest.team_count}팀</span>
                  </p>
                </article>
              ))}
              {visibleContests.length === 0 && !status && (
                <p className="empty-hint">
                  {contests.length === 0 ? '아직 등록된 대회가 없습니다.' : '조건에 맞는 대회가 없습니다.'}
                </p>
              )}
            </section>
          </>
        )}
      </main>

      <footer className="site-footer">
        <p id="sync-status">{status}</p>
      </footer>
    </>
  );
}

interface AccountMenuProps {
  username: string;
  isOrganizer: boolean;
  onLogout: () => void;
}

/**
 * 좁은 화면 전용 계정 메뉴. 헤더 한 줄에 이름·역할·로그아웃까지 넣으면 390px를 넘어서
 * 아바타 하나만 두고 나머지는 눌렀을 때 펼친다. 넓은 화면에서는 CSS 로 숨고 .auth-status 가 보인다.
 */
function AccountMenu({ username, isOrganizer, onLogout }: AccountMenuProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // 메뉴 밖을 누르거나 Esc 를 누르면 닫는다.
  useEffect(() => {
    if (!open) return;
    const onPointer = (e: PointerEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    document.addEventListener('pointerdown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div className="account-menu" ref={ref}>
      <button
        type="button"
        className="account-trigger"
        aria-label="계정 메뉴"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="avatar" aria-hidden="true">
          {username.slice(0, 1).toUpperCase()}
        </span>
      </button>
      {open && (
        <div className="account-popover">
          <span className="auth-name">{username}</span>
          <span className="auth-role">{isOrganizer ? '운영자' : '참가자'}</span>
          <button type="button" onClick={onLogout}>
            로그아웃
          </button>
        </div>
      )}
    </div>
  );
}
