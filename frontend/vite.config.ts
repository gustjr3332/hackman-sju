import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  // iOS 앱 빌드(`npm run ios:sync` = --mode ios)는 .env.ios.local 의 운영 주소를 써야 한다.
  // 빠뜨리면 기본 .env 의 로컬 Supabase(127.0.0.1)를 가리키는 앱이 심사에 올라간다.
  if (mode === 'ios') {
    const env = loadEnv(mode, process.cwd());
    if (!env.VITE_SUPABASE_URL?.startsWith('https://') || !env.VITE_SITE_URL?.startsWith('https://')) {
      throw new Error('iOS 빌드: .env.ios.local 에 VITE_SUPABASE_URL·VITE_SITE_URL(https://…)을 넣으세요.');
    }
  }
  return { plugins: [react()] };
});
