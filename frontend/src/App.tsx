import { useCallback, useEffect, useState } from 'react';
import {
  AUTH_EXPIRED_EVENT,
  fetchContests,
  fetchMe,
  getStoredUsername,
  logout,
  onStoredUsernameChange,
  storeUsername,
} from './api';
import { AuthPanel } from './AuthPanel';
import { ContestDetail } from './ContestDetail';
import { ContestForm } from './ContestForm';
import { STATUS_LABEL } from './labels';
import { ThemeToggle } from './ThemeToggle';
import type { Contest } from './types';

export default function App() {
  const [contests, setContests] = useState<Contest[]>([]);
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [status, setStatus] = useState('불러오는 중…');
  const [username, setUsername] = useState<string | null>(getStoredUsername());
  const [isOrganizer, setIsOrganizer] = useState(false);
  const [showCreateForm, setShowCreateForm] = useState(false);

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
  function handleLogout() {
    logout();
    setUsername(null);
    setStatus('');
    loadContests();
  }

  function handleLoggedIn(name: string) {
    setUsername(name);
    setStatus('');
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
    setSelectedSlug(created.slug);
    loadContests();
  }

  return (
    <>
      <header className="site-header">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            H
          </span>
          <span className="brand-word">HACKMAN</span>
          {selected && (
            <>
              <span className="crumb-sep" aria-hidden="true">
                /
              </span>
              <span className="crumb">{selected.name}</span>
            </>
          )}
        </div>
        <div className="header-right">
          <ThemeToggle />
          {username && (
            <div className="auth-status">
              <span>
                {username}
                {isOrganizer && <span className="role-tag">운영자</span>}
              </span>
              <button type="button" onClick={handleLogout}>
                로그아웃
              </button>
            </div>
          )}
        </div>
      </header>

      <main className="main-content">
        {!username && <AuthPanel onLoggedIn={handleLoggedIn} />}

        {selected ? (
          <ContestDetail
            contest={selected}
            username={username}
            isOrganizer={isOrganizer}
            onBack={() => setSelectedSlug(null)}
            onContestUpdated={handleContestUpdated}
          />
        ) : (
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

            <section className="contest-list">
              {contests.map((contest) => (
                <article
                  key={contest.slug}
                  className={`contest-card status-${contest.status}`}
                  onClick={() => setSelectedSlug(contest.slug)}
                >
                  <div className="contest-card-main">
                    <h2>{contest.name}</h2>
                    <p className="contest-meta">
                      <span>
                        {contest.start_at.slice(0, 10)} – {contest.end_at.slice(0, 10)}
                      </span>
                      <span>{contest.team_count}팀</span>
                    </p>
                  </div>
                  <span className={`status-badge status-${contest.status}`}>
                    {STATUS_LABEL[contest.status]}
                  </span>
                </article>
              ))}
              {contests.length === 0 && !status && (
                <p className="empty-hint">아직 등록된 대회가 없습니다.</p>
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
