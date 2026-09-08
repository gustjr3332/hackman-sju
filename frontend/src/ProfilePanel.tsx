import { useEffect, useState } from 'react';
import { extractMyProfile, fetchLlmProviders, fetchMyProfile, updateMyProfile } from './api';
import type { LlmProvider, Profile } from './types';

const ROLE_LABEL: Record<string, string> = {
  frontend: '프론트엔드',
  backend: '백엔드',
  mobile: '모바일',
  design: '디자인',
  data: '데이터',
  planning: '기획',
  ai: 'AI',
};

const LEVEL_LABEL: Record<string, string> = {
  '': '미입력',
  beginner: '입문',
  intermediate: '중급',
  advanced: '숙련',
};

/** 쉼표로 구분한 문자열 ↔ 태그 배열. 참가자가 직접 고칠 때 가장 손이 덜 간다. */
function toTags(text: string): string[] {
  return [...new Set(text.split(',').map((t) => t.trim().toLowerCase()).filter(Boolean))];
}

interface ProfilePanelProps {
  /** 프로필이 저장·정리될 때마다 알린다 — 추천이 이 프로필을 기준으로 계산되기 때문이다. */
  onChanged: () => void;
}

/**
 * 팀빌딩 프로필 편집기.
 *
 * 자유 서술 원문이 사실의 원본이고 태그는 거기서 파생된 것이다. 자동 정리(LLM)는 태그를
 * 채워주기만 하고, **결과는 참가자가 그대로 고칠 수 있다** — 모델이 정한 것을 사실로 굳히지
 * 않는다. LLM 키가 하나도 없으면 자동 정리 버튼만 사라지고 나머지는 그대로 쓴다.
 */
export function ProfilePanel({ onChanged }: ProfilePanelProps) {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [providers, setProviders] = useState<LlmProvider[]>([]);
  const [provider, setProvider] = useState('');
  const [intro, setIntro] = useState('');
  const [skillsText, setSkillsText] = useState('');
  const [interestsText, setInterestsText] = useState('');
  const [githubUrl, setGithubUrl] = useState('');
  const [roles, setRoles] = useState<string[]>([]);
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);

  function apply(next: Profile) {
    setProfile(next);
    setIntro(next.intro);
    setGithubUrl(next.github_url);
    setSkillsText(next.skills.join(', '));
    setInterestsText(next.interests.join(', '));
    setRoles(next.roles);
  }

  useEffect(() => {
    fetchMyProfile().then(apply).catch(() => setStatus('프로필을 불러오지 못했습니다'));
    // 키가 설정된 제공사가 없으면 빈 배열이 온다 — 그때는 자동 정리 UI 자체를 숨긴다.
    fetchLlmProviders()
      .then(({ providers: list }) => {
        setProviders(list);
        if (list.length) setProvider(list[0].provider);
      })
      .catch(() => setProviders([]));
  }, []);

  async function save() {
    setStatus('');
    setBusy(true);
    try {
      apply(
        await updateMyProfile({
          intro,
          github_url: githubUrl.trim(),
          skills: toTags(skillsText),
          interests: toTags(interestsText),
          roles,
          looking_for_team: profile?.looking_for_team ?? true,
        })
      );
      onChanged();
      setStatus('저장했습니다');
    } catch (err) {
      setStatus(err instanceof Error ? err.message : '저장에 실패했습니다');
    } finally {
      setBusy(false);
    }
  }

  async function autoFill() {
    setStatus('');
    setBusy(true);
    try {
      // 원문이 저장돼 있어야 서버가 그걸 읽어 정리한다.
      await updateMyProfile({ intro });
      const next = await extractMyProfile(provider || undefined);
      apply(next);
      onChanged();
      setStatus(
        next.extraction_status === 'done'
          ? '자동으로 정리했습니다 — 틀린 부분은 직접 고치세요'
          : `자동 정리에 실패했습니다: ${next.extraction_error}`
      );
    } catch (err) {
      setStatus(err instanceof Error ? err.message : '자동 정리에 실패했습니다');
    } finally {
      setBusy(false);
    }
  }

  async function toggleLooking() {
    if (!profile) return;
    apply(await updateMyProfile({ looking_for_team: !profile.looking_for_team }));
    onChanged();
  }

  if (!profile) return null;

  if (!open) {
    return (
      <div className="profile-panel collapsed">
        <button type="button" className="profile-toggle" onClick={() => setOpen(true)}>
          내 팀빌딩 프로필
        </button>
        <span className="empty-hint">
          {profile.skills.length
            ? `${profile.skills.slice(0, 3).join(', ')}${profile.skills.length > 3 ? ' 외' : ''}`
            : '아직 비어 있습니다 — 채우면 맞는 팀을 추천받습니다'}
        </span>
      </div>
    );
  }

  return (
    <div className="profile-panel">
      <div className="profile-head">
        <h3 className="section-heading">내 팀빌딩 프로필</h3>
        <button type="button" onClick={() => setOpen(false)}>
          접기
        </button>
      </div>

      <label className="profile-field">
        <span>자기소개 (자유롭게)</span>
        <textarea
          value={intro}
          onChange={(e) => setIntro(e.target.value)}
          rows={4}
          placeholder="예) 웹 프론트 좀 했고 파이썬도 조금 압니다. 디자인도 관심 있어요."
        />
      </label>

      <label className="profile-field">
        <span>GitHub 주소 (선택)</span>
        <input
          type="url"
          value={githubUrl}
          onChange={(e) => setGithubUrl(e.target.value)}
          placeholder="https://github.com/아이디"
          autoComplete="off"
        />
      </label>

      {providers.length > 0 && (
        <div className="profile-autofill">
          <button type="button" onClick={autoFill} disabled={busy || !intro.trim()}>
            자기소개에서 자동 정리
          </button>
          {providers.length > 1 && (
            <select value={provider} onChange={(e) => setProvider(e.target.value)}>
              {providers.map((p) => (
                <option key={p.provider} value={p.provider}>
                  {p.label}
                </option>
              ))}
            </select>
          )}
          <span className="empty-hint">정리된 결과는 아래에서 직접 고칠 수 있습니다.</span>
        </div>
      )}

      <label className="profile-field">
        <span>기술 스택 (쉼표로 구분)</span>
        <input
          type="text"
          value={skillsText}
          onChange={(e) => setSkillsText(e.target.value)}
          placeholder="react, python, figma"
        />
      </label>

      <label className="profile-field">
        <span>관심 분야 (쉼표로 구분)</span>
        <input
          type="text"
          value={interestsText}
          onChange={(e) => setInterestsText(e.target.value)}
          placeholder="교육, 헬스케어"
        />
      </label>

      <fieldset className="profile-roles">
        <legend>맡고 싶은 역할</legend>
        {Object.entries(ROLE_LABEL).map(([value, label]) => (
          <label key={value}>
            <input
              type="checkbox"
              checked={roles.includes(value)}
              onChange={(e) =>
                setRoles((prev) =>
                  e.target.checked ? [...prev, value] : prev.filter((r) => r !== value)
                )
              }
            />
            {label}
          </label>
        ))}
      </fieldset>

      <label className="profile-looking">
        <input
          type="checkbox"
          checked={profile.looking_for_team}
          onChange={toggleLooking}
        />
        팀을 찾는 중입니다 (다른 팀의 후보 목록에 표시)
      </label>

      <div className="profile-actions">
        <button type="button" onClick={save} disabled={busy}>
          {busy ? '저장 중…' : '저장'}
        </button>
        {profile.level && <span className="empty-hint">숙련도: {LEVEL_LABEL[profile.level]}</span>}
      </div>
      {status && <p className="empty-hint">{status}</p>}
    </div>
  );
}
