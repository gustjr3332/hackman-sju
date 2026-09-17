import { useState } from 'react';
import { login, register, requestPasswordReset, setNewPassword } from './api';

interface AuthPanelProps {
  onLoggedIn: (username: string) => void;
}

type Mode = 'login' | 'register' | 'reset';

export function AuthPanel({ onLoggedIn }: AuthPanelProps) {
  const [mode, setMode] = useState<Mode>('login');
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);

  const isRegister = mode === 'register';
  const isReset = mode === 'reset';
  // 서버에 보내기 전에 입력 즉시 알려 준다. 실제 강제는 서버가 한다.
  const passwordTooShort = isRegister && password.length > 0 && password.length < 8;
  const confirmMismatch = isRegister && confirm.length > 0 && confirm !== password;
  const filled = isReset
    ? email.length > 0
    : email.length > 0 && password.length > 0 && (!isRegister || (username.length > 0 && confirm.length > 0));
  const canSubmit = filled && !passwordTooShort && !confirmMismatch && !busy;

  function switchMode(next: Mode) {
    setMode(next);
    setError('');
    setNotice('');
    setConfirm('');
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    setNotice('');
    setBusy(true);
    try {
      if (isReset) {
        await requestPasswordReset(email);
        setNotice('재설정 메일을 보냈습니다. 메일의 링크를 누르면 새 비밀번호를 정할 수 있습니다.');
        return;
      }
      if (isRegister) await register(username, email, password);
      onLoggedIn(await login(email, password));
    } catch (err) {
      setError(err instanceof Error ? err.message : '요청에 실패했습니다');
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="auth-panel" onSubmit={handleSubmit}>
      <div className="auth-tabs">
        <button type="button" className={mode === 'login' ? 'active' : ''} onClick={() => switchMode('login')}>
          로그인
        </button>
        <button type="button" className={isRegister ? 'active' : ''} onClick={() => switchMode('register')}>
          회원가입
        </button>
      </div>

      {isRegister && (
        <input
          type="text"
          placeholder="아이디 (팀원·심사위원에게 보이는 이름)"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoComplete="username"
          required
        />
      )}
      <input
        type={mode === 'login' ? 'text' : 'email'}
        placeholder={mode === 'login' ? '아이디 또는 이메일' : '이메일'}
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        autoComplete={mode === 'login' ? 'username' : 'email'}
        required
      />
      {!isReset && (
        <input
          type="password"
          placeholder={isRegister ? '비밀번호 (8자 이상)' : '비밀번호'}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete={isRegister ? 'new-password' : 'current-password'}
          minLength={8}
          required
        />
      )}
      {passwordTooShort && <p className="field-note">비밀번호는 8자 이상이어야 합니다</p>}
      {isRegister && (
        <input
          type="password"
          placeholder="비밀번호 확인"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          autoComplete="new-password"
          required
        />
      )}
      {confirmMismatch && <p className="field-note">비밀번호가 일치하지 않습니다</p>}

      {error && <p className="form-error">{error}</p>}
      {notice && <p className="field-note">{notice}</p>}

      <button type="submit" disabled={!canSubmit}>
        {busy ? '확인 중…' : isReset ? '재설정 메일 보내기' : isRegister ? '가입하고 시작하기' : '로그인'}
      </button>

      {mode === 'login' && (
        <button type="button" className="link-btn" onClick={() => switchMode('reset')}>
          비밀번호를 잊으셨나요?
        </button>
      )}
      {isReset && (
        <button type="button" className="link-btn" onClick={() => switchMode('login')}>
          로그인으로 돌아가기
        </button>
      )}

      <p className="auth-footnote">
        가입한 계정은 참가자이고, 심사위원은 운영자가 배정하며 운영자 권한은 관리자가 부여합니다.
        대회 목록과 스코어보드는 로그인 없이 볼 수 있습니다.
      </p>
    </form>
  );
}

/** 재설정 메일의 링크로 돌아왔을 때 새 비밀번호를 정한다. */
export function NewPasswordPanel({ onDone }: { onDone: (username: string) => void }) {
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      onDone(await setNewPassword(password));
    } catch (err) {
      setError(err instanceof Error ? err.message : '비밀번호를 바꾸지 못했습니다');
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="auth-panel" onSubmit={handleSubmit}>
      <p className="field-note">새 비밀번호를 정해 주세요.</p>
      <input
        type="password"
        placeholder="새 비밀번호 (8자 이상)"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        autoComplete="new-password"
        minLength={8}
        required
      />
      {error && <p className="form-error">{error}</p>}
      <button type="submit" disabled={busy || password.length < 8}>
        {busy ? '저장 중…' : '비밀번호 저장'}
      </button>
    </form>
  );
}
