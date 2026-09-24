// HACKMAN 서비스 워커. 하는 일은 하나다: 한 번 받은 앱 껍데기(index.html + 빌드 자산)를
// 보관해 두었다가 네트워크가 끊겨도 화면을 띄운다. 설치형 앱과 스토어 포장(TWA)에서 브라우저
// 기본 오프라인 화면 대신 앱이 뜨게 하려는 것이다.
//
// 대회 데이터(Supabase)는 캐시하지 않는다. 실시간 점수가 오래된 값으로 보이면 안 되므로 다른
// 출처 요청은 아예 건드리지 않는다.
const CACHE = 'hackman-shell-v1';

self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // 화면 이동은 네트워크 먼저 — 배포 직후 새 버전이 바로 보여야 한다. 끊겼을 때만 보관본.
  // 경로가 무엇이든 SPA 라 받는 것은 같은 index.html 이므로 '/' 하나로 보관한다.
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok) {
            const copy = res.clone();
            event.waitUntil(saveShell(copy));
          }
          return res;
        })
        .catch(() => caches.match('/'))
    );
    return;
  }

  // 빌드 자산은 내용이 바뀌면 파일 이름(해시)도 바뀌므로 보관본을 먼저 쓴다.
  if (url.pathname.startsWith('/assets/') || url.pathname.startsWith('/icons/')) {
    event.respondWith(
      caches.match(req).then(
        (hit) =>
          hit ||
          fetch(req).then((res) => {
            if (res.ok) {
              const copy = res.clone();
              event.waitUntil(caches.open(CACHE).then((c) => c.put(req, copy)));
            }
            return res;
          })
      )
    );
  }
});

// 새 index.html 을 보관하면서, 그 html 이 더는 가리키지 않는 옛 빌드 자산을 지운다.
// 이렇게 안 하면 배포할 때마다 이전 번들이 캐시에 쌓인다.
async function saveShell(res) {
  const cache = await caches.open(CACHE);
  const html = await res.clone().text();
  await cache.put('/', res);
  const keys = await cache.keys();
  await Promise.all(
    keys
      .filter((k) => {
        const path = new URL(k.url).pathname;
        return path.startsWith('/assets/') && !html.includes(path);
      })
      .map((k) => cache.delete(k))
  );
}
