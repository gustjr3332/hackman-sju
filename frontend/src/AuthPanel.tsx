import { useState } from 'react';
import { login, register } from './api';

interface AuthPanelProps {
  onLoggedIn: (username: string) => void;
}

export function AuthPanel({ onLoggedIn }: AuthPanelProps) {
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const isRegister = mode === 'register';
  // 서버에 보내기 전에 입력 즉시 알려 준다. 실제 강제는 서버가 한다.
  const passwordTooShort = isRegister && password.length > 0 && password.length < 8;
  const confirmMismatch = isRegister && confirm.length > 0 && confirm !== password;
  const filled = username.length > 0 && password.length > 0 && (!isRegister || confirm.length > 0);
  const canSubmit = filled && !passwordTooShort && !confirmMismatch && !busy;

  function switchMode(next: 'login' | 'register') {
    setMode(next);
    setError('');
    setConfirm('');
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    setBusy(true);
    try {
      if (isRegister) {
        await register(username, email, password);
      }
      await login(username, password);
      onLoggedIn(username);
    } catch (err) {
      setError(err instanceof Error ? err.message : '요청에 실패했습니다');
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="auth-panel" onSubmit={handleSubmit}>
      <div className="auth-tabs">
        <button
          type="button"
          className={mode === 'login' ? 'active' : ''}
          onClick={() => switchMode('login')}
        >
          로그인
        </button>
        <button
          type="button"
          className={mode === 'register' ? 'active' : ''}
          onClick={() => switchMode('register')}
        >
          회원가입
        </button>
      </div>

      <input
        type="text"
        placeholder="아이디"
        value={username}
        onChange={(e) => setUsername(e.target.value)}
        required
      />
      {isRegister && (
        <input
          type="email"
          placeholder="이메일 (선택)"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
      )}
      <input
        type="password"
        placeholder={isRegister ? '비밀번호 (8자 이상)' : '비밀번호'}
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        minLength={8}
        required
      />
      {passwordTooShort && <p className="field-note">비밀번호는 8자 이상이어야 합니다</p>}
      {isRegister && (
        <input
          type="password"
          placeholder="비밀번호 확인"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          required
        />
      )}
      {confirmMismatch && <p className="field-note">비밀번호가 일치하지 않습니다</p>}

      {error && <p className="form-error">{error}</p>}

      <button type="submit" disabled={!canSubmit}>
        {busy ? '확인 중…' : isRegister ? '가입하고 시작하기' : '로그인'}
      </button>

      <p className="auth-footnote">
        가입한 계정은 참가자입니다. 심사위원은 운영자가 배정하고, 운영자 권한은 관리자가
        부여합니다. 대회 목록과 스코어보드는 로그인 없이 볼 수 있습니다.
      </p>
    </form>
  );
}
