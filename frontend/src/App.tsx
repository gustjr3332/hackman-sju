import { useCallback, useEffect, useState } from 'react';
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
  // 로그인 폼을 접고 관람만 하는 상태. 로그인하면 의미가 없어지므로 함께 해제한다.
  const [browsing, setBrowsing] = useState(false);
  const [recovering, setRecovering] = useState(false);

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
    setBrowsing(false);
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

  const needle = query.trim().toLowerCase();
  const visibleContests = contests.filter(
    (c) =>
      (statusFilter === 'all' || c.status === statusFilter) &&
      (!needle || c.name.toLowerCase().includes(needle))
  );

  return (
    <>
      <header className="site-header">
        <div className="brand">
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
          {!username && !browsing && (
            <button type="button" className="link-btn" onClick={() => setBrowsing(true)}>
              로그인 없이 둘러보기
            </button>
          )}
          <ThemeToggle />
          {username && (
            <div className="auth-status">
              {/* 이니셜 아바타 + 이름/역할 2줄 (DESIGN.md 헤더 규격). */}
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
        </div>
      </header>

      <main className="main-content">
        {recovering && (
          <NewPasswordPanel
            onDone={(name) => {
              setRecovering(false);
              handleLoggedIn(name);
            }}
          />
        )}

        {/* 관람자는 로그인 없이 목록·스코어보드를 볼 수 있다. 패널을 접어 두면 대회가
            바로 보이고, 필요할 때 헤더에서 다시 연다. */}
        {!username &&
          (browsing ? (
            <p className="guest-note">
              둘러보는 중입니다 — 팀 참가·제출·채점은 로그인이 필요합니다.{' '}
              <button type="button" className="link-btn" onClick={() => setBrowsing(false)}>
                로그인하기
              </button>
            </p>
          ) : (
            <AuthPanel onLoggedIn={handleLoggedIn} />
          ))}

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
                  {/* 상태 점·라벨 → 대회명 → 한 줄 설명 → 헤어라인 → 모노 메타 (DESIGN.md 카드 규격). */}
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
        <p className="site-credit">HACKMAN · Supabase + React</p>
      </footer>
    </>
  );
}
