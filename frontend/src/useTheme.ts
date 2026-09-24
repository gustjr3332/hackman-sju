import { useCallback, useEffect, useState } from 'react';

export type Theme = 'light' | 'dark';

const STORAGE_KEY = 'hackman-theme';

function systemPrefersDark() {
  return window.matchMedia('(prefers-color-scheme: dark)').matches;
}

function readStoredTheme(): Theme | null {
  const stored = localStorage.getItem(STORAGE_KEY);
  return stored === 'light' || stored === 'dark' ? stored : null;
}

// 설치형 앱의 상태 표시줄 색. style.css 의 --paper 와 같은 값이고, index.html 의
// theme-color 두 줄(시스템 라이트/다크용)을 직접 고른 테마 색으로 덮거나 되돌린다.
const PAPER: Record<Theme, string> = { light: '#f3f4f1', dark: '#14161a' };

function applyTheme(theme: Theme | null) {
  if (theme) {
    document.documentElement.setAttribute('data-theme', theme);
  } else {
    document.documentElement.removeAttribute('data-theme');
  }
  document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]').forEach((meta) => {
    const system: Theme = meta.media.includes('dark') ? 'dark' : 'light';
    meta.content = PAPER[theme ?? system];
  });
}

// null = 시스템 설정을 따름 (사용자가 아직 명시적으로 고르지 않음)
export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(() => readStoredTheme() ?? (systemPrefersDark() ? 'dark' : 'light'));

  // 다른 탭에서 테마를 바꾸면 이 탭도 맞춘다.
  useEffect(() => {
    const handleStorage = (e: StorageEvent) => {
      if (e.key !== STORAGE_KEY) return;
      const next = e.newValue === 'light' || e.newValue === 'dark' ? e.newValue : null;
      applyTheme(next);
      setThemeState(next ?? (systemPrefersDark() ? 'dark' : 'light'));
    };
    window.addEventListener('storage', handleStorage);
    return () => window.removeEventListener('storage', handleStorage);
  }, []);

  const setTheme = useCallback((next: Theme) => {
    localStorage.setItem(STORAGE_KEY, next);
    applyTheme(next);
    setThemeState(next);
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme(theme === 'dark' ? 'light' : 'dark');
  }, [theme, setTheme]);

  return { theme, toggleTheme };
}
