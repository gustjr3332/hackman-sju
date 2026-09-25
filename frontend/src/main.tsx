import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
// UI 서체(Pretendard, 번들에 포함). 화면에 쓰인 글자 범위의 조각 파일만 받는다.
import 'pretendard/dist/web/variable/pretendardvariable-dynamic-subset.css';
import './style.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>
);

// 오프라인에서도 앱 화면이 뜨게 하는 서비스 워커(public/sw.js). 배포본에서만 등록한다 —
// 개발 서버는 파일이 수시로 바뀌어 보관본이 오히려 방해된다. iOS 앱(capacitor://)에서는
// 지원되지 않아 등록이 실패하고 catch 가 삼킨다(앱은 파일을 안에 들고 있어 필요도 없다).
if (import.meta.env.PROD && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => undefined);
  });
}
