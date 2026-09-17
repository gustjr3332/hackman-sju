import { useEffect, useState } from 'react';

// 주소창 경로. 갤러리·프로젝트 상세는 링크를 주고받는 화면이라 URL 이 있어야 한다.
// 경로가 네 개뿐이라 라우터 라이브러리 대신 History API 를 직접 쓴다.
// 배포(Vercel)에서는 깊은 경로 새로고침이 index.html 로 가도록 frontend/vercel.json 이 받는다.

export type Route =
  | { name: 'list' }
  | { name: 'contest'; slug: string }
  | { name: 'gallery'; slug: string }
  | { name: 'project'; slug: string; teamId: number };

function parseRoute(path: string): Route {
  const [, c, slug, section, id] = path.split('/');
  if (c !== 'c' || !slug) return { name: 'list' };
  const s = decodeURIComponent(slug);
  if (section === 'gallery') return { name: 'gallery', slug: s };
  if (section === 'projects' && Number(id) > 0) return { name: 'project', slug: s, teamId: Number(id) };
  return { name: 'contest', slug: s };
}

export const paths = {
  list: () => '/',
  contest: (slug: string) => `/c/${encodeURIComponent(slug)}`,
  gallery: (slug: string) => `/c/${encodeURIComponent(slug)}/gallery`,
  project: (slug: string, teamId: number) => `/c/${encodeURIComponent(slug)}/projects/${teamId}`,
};

export function navigate(to: string) {
  if (to === window.location.pathname) return;
  window.history.pushState(null, '', to);
  window.dispatchEvent(new PopStateEvent('popstate'));
  window.scrollTo(0, 0);
}

export function useRoute(): Route {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);
  return parseRoute(path);
}
