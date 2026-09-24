import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import './style.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>
);

// 오프라인에서도 앱 화면이 뜨게 하는 서비스 워커(public/sw.js). 배포본에서만 등록한다 —
// 개발 서버는 파일이 수시로 바뀌어 보관본이 오히려 방해된다. iOS 앱(capacitor://)은 파일을
// 앱 안에 들고 있어 필요 없고, WKWebView 가 그 스킴에서 서비스 워커를 지원하지도 않는다.
if (import.meta.env.PROD && location.protocol.startsWith('http') && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => undefined);
  });
}
