import { useEffect, useState } from 'react';

function pad(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

/** targetIso 까지 남은 시간을 "HH:MM:SS" 로 쪼갠다. 지났으면 모두 0. */
function splitRemaining(targetIso: string, now: Date) {
  const diffMs = new Date(targetIso).getTime() - now.getTime();
  const totalSeconds = Math.max(0, Math.floor(diffMs / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return { totalSeconds, hours, minutes, seconds };
}

interface CountdownTimerProps {
  targetIso: string;
  /** 카운트다운이 0에 도달했을 때 보여줄 문구. */
  expiredLabel: string;
  /** 남은 시간 앞에 붙는 라벨, 예: "대회 종료까지". */
  label: string;
}

/** 초 단위로 갱신되는 잔여 시간 표시(시간·분·초 타일). 24시간 넘게 남으면 시간 칸이 계속 늘어난다. */
export function CountdownTimer({ targetIso, expiredLabel, label }: CountdownTimerProps) {
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const { totalSeconds, hours, minutes, seconds } = splitRemaining(targetIso, now);
  const urgent = totalSeconds > 0 && totalSeconds <= 300; // 5분 이하면 강조

  return (
    <div className={`countdown-timer${urgent ? ' urgent' : ''}${totalSeconds === 0 ? ' expired' : ''}`}>
      <span className="countdown-label">{totalSeconds === 0 ? expiredLabel : label}</span>
      {totalSeconds > 0 && (
        <span className="countdown-tiles" role="timer">
          <span><b>{pad(hours)}</b>시간</span>
          <span><b>{pad(minutes)}</b>분</span>
          <span><b>{pad(seconds)}</b>초</span>
        </span>
      )}
    </div>
  );
}
