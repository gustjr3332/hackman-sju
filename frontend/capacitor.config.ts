import type { CapacitorConfig } from '@capacitor/cli';

// iOS 앱 포장(App Store). 웹 빌드(dist)를 앱 안에 넣어 띄운다. 안드로이드는 Capacitor 가 아니라
// TWA(Bubblewrap)로 웹 주소를 그대로 띄운다 — DEVELOPMENT.md "스토어 배포 준비".
// appId 는 도메인(.com) 확정 후 그 역도메인으로 바꾼다. App Store 에 처음 올리기 전까지만 바꿀 수 있다.
const config: CapacitorConfig = {
  appId: 'com.example.hackman',
  appName: 'HACKMAN',
  webDir: 'dist',
};

export default config;
