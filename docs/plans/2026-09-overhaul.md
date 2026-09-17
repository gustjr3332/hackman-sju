# HACKMAN 대폭 개편 계획: DEVPOST 구성안 디자인 + Supabase 이전

## Context

요청은 네 가지다. (1) 'DEVPOST 기반' 아티팩트를 참고해 디자인 전체 개편, (2) Vercel·Render를
전부 Supabase로 옮길 수 있는지 확인하고 방법 설명, (3) archify로 현재 구조를 먼저 그린 뒤
이전 진행, (4) 코드 작업은 **차주 주말**에 하고 지금은 계획만. 사용자는 그 전에 Claude 관련
폴더를 크게 정리할 예정이다.

지금 상태(2026-09-14 확인): `main` 은 origin 과 동기화, 테스트 155건 통과, Render·Vercel 모두
최신 커밋 배포됨. 다만 Render 는 잠든 상태에서 첫 응답에 **43초**가 걸렸다. 워커 1개와
콜드스타트가 지금까지 설계 판단(LLM 을 요청 경로에서 빼기, 폴링+캐시)을 가장 많이 제약해 왔다.

### 조사로 확인한 사실 (Supabase, 2026-09 기준)

| 구성요소 | 지금 | Supabase 에서 | 판정 |
|---|---|---|---|
| 프론트 (Vite SPA) | Vercel | 공식 호스팅 없음. Storage 에 올린 HTML 은 보안상 `text/plain` 으로 내려감. 우회는 Pro + 커스텀 도메인 | **Vercel 유지** |
| REST API (Django+DRF) | Render, gunicorn 워커 1개 | Python 실행 불가. PostgREST 자동 API + RLS + Postgres 함수(RPC) | **재작성** |
| LLM·GitHub 프록시·심사 보조 스레드 | Django 안 | Edge Functions: TypeScript/Deno, 벽시계 150초(무료), CPU 2초, 메모리 256MB, `EdgeRuntime.waitUntil` 로 응답 후 계속 실행 | **재작성** |
| 인증 (simplejwt) | Django `auth_user` | Supabase Auth. 가져올 수 있는 해시는 bcrypt·Argon2뿐, Django 기본 PBKDF2 는 불가 | **기존 회원 비밀번호 재설정** |
| 스코어보드 5초 폴링 + ETag + LocMemCache | Django | Realtime "DB에서 브로드캐스트"(`realtime.send`) | **대체, 개선** |
| Django admin (스택 목록 관리 등) | `/admin/` | Supabase Studio | 대체 |
| Postgres | 이미 Supabase(세션 풀러) | 그대로. 스키마만 새로 | 유지 |

출처: supabase.com/docs (functions/limits, functions/background-tasks, realtime/broadcast,
migrating-to-supabase/auth0), github.com/orgs/supabase/discussions/47156(프론트 호스팅 공식 답변),
discussions/2557·39110(Storage HTML text/plain).

### 이번에 정한 것 (사용자 답변)

1. 백엔드는 **Supabase 네이티브로 재작성**한다. Django 는 전환 후 폐기.
2. 프론트는 **Vercel 유지**.
3. 디자인은 **구성안의 구조 + DESIGN.md 의 시각 규칙**. 목록 3열 카드 그리드·밑줄 탭은 그대로.
4. 제출물 갤러리는 **전체 공개, 점수는 예선만**(지금 스코어보드 규칙과 같다). 결선 점수와 심사
   보조 분석은 계속 비공개.

---

## 작업 순서와 이유

| 단계 | 내용 | 시점 | 규모 |
|---|---|---|---|
| 0 | 착수 준비 + archify 구조도 | 주말 1 시작 | 2~3시간 |
| A | 디자인 개편 (지금 Django 위에서) | 주말 1 | 약 8시간 |
| B | Supabase 스키마·RLS·RPC·Auth·Realtime | 주말 2 | 약 10시간 |
| C | Edge Functions + 프론트 데이터 계층 교체 | 주말 3 | 약 10시간 |
| D | 데이터 이전, 전환, Render 종료 | 주말 3 마무리 | 반나절 |

**디자인을 먼저 하는 이유:** 컴포넌트는 `frontend/src/api.ts` 의 함수(44개)만 부른다. 이 함수
이름과 반환 타입을 계약으로 고정하면, A 에서 만든 화면은 C 에서 백엔드가 바뀌어도 손대지 않는다.
재작성이 늦어져도 새 디자인은 먼저 배포돼 있다.

---

## 0. 착수 준비

1. **이 계획을 저장소로 복사한다:** `docs/plans/2026-09-overhaul.md`. 원본이 `~/.claude/plans/`
   에 있어 Claude 폴더 정리 때 사라질 수 있다. (승인 직후 바로 해도 되는 유일한 작업)
2. 스킬 확인: `ls ~/.claude/skills/archify`. 없으면 `npx skills add tt-a1i/archify -g` 재설치.
3. 브랜치 `overhaul/devpost-supabase` 생성. `docs/seminar-prep.md`(미추적)는 이 작업과 무관하니 건드리지 않는다.

### archify 구조도 3장

출력 위치 `docs/architecture/` (JSON 스펙 + HTML). 본문은 한국어로 쓰고, archify 뷰어 UI 는
`en`/`zh-CN` 만 지원하므로 **뷰어 버튼·메뉴는 영어로 나온다**는 점을 남긴다.

| 파일 | 타입 | 담을 사실 (코드 근거) |
|---|---|---|
| `as-is.architecture.json` | architecture | Vercel SPA → Render gunicorn 워커 1개(`backend/Procfile`) → Supabase Postgres 세션 풀러 5432. Django 내부: DRF API, LocMemCache(스코어보드 3초·GitHub 1800초), 백그라운드 스레드(`judge_assist.run_in_background`), LLM 3사(`llm/base.py`), GitHub API 프록시(`github.py`). GitHub Actions keepalive·backup |
| `to-be.architecture.json` | architecture | Vercel SPA → supabase-js → PostgREST(RLS) / RPC / Realtime 브로드캐스트 / Auth / Edge Functions(LLM·GitHub·분석) → Postgres. 외부: LLM 3사, GitHub API |
| `migration.workflow.json` | workflow (schema v2) | 0~D 단계와 각 단계의 통과 조건, 전환 후 1주 롤백 창 |

절차(스킬 규칙): 스펙 작성 → `node bin/archify.mjs validate <type> <spec> --quality showcase --json`
(9개 검사 통과·경고 0) → `deliver` → `visual-check`. 실패하면 진단된 항목만 고친다.

---

## A. 디자인 개편 (지금 백엔드 위에서)

참고 아티팩트: https://claude.ai/code/artifact/e9d26ec3-45ec-4ac2-a9bf-c9cf6568346b
("HACKMAN 대회 탐색 구성안", 화면 3개: 목록 검색 → 제출물 갤러리 → 프로젝트 상세)

1. **`DESIGN.md` 를 먼저 개정한다.** 이 문서가 단일 기준이라 화면보다 먼저 고친다.
   - 참조 캔버스에 DEVPOST 구성안 링크 추가
   - 화면별 기준 표에 `제출물 갤러리`, `프로젝트 상세` 두 행 추가
   - 대회 목록: 기존 카드 그리드 위에 검색창 추가(구성안), 상태 필터는 밑줄 탭 유지(DESIGN.md)
   - 갤러리 타일: 카드 규칙 적용(`--surface`, 헤어라인, 좌측 레일, 6px, 그림자 없음). 점수는
     모노·우측. 점수 있는 팀만 레일에 `--signal`. 미제출 팀은 흐리게 + "미제출"
   - 프로젝트 상세: 본문(소개 → 데모 → 코드) + 우측 사이드(팀원·리포지토리·데모 링크).
     구성안의 "트랙" 칸은 모델에 필드가 없어 넣지 않는다
2. **라우팅 추가.** 지금은 `App.tsx` 의 `selectedSlug` 상태로만 화면이 바뀌어 링크 공유가 안 된다.
   갤러리·상세는 링크를 주고받는 화면이라 URL 이 필요하다.
   - `react-router-dom` 추가, `frontend/vercel.json` 에 SPA rewrite(`/(.*) → /index.html`)
   - 경로: `/`, `/c/:slug`, `/c/:slug/gallery`, `/c/:slug/projects/:teamId`
3. **새 화면**
   - `frontend/src/Gallery.tsx`: 기존 `fetchTeams(slug)` 와 스코어보드 응답을 합쳐 타일 그리드.
     예선/결선 밑줄 탭, 결선 탭은 스코어보드와 같은 권한 규칙(`canSeeFinal`)으로만 노출.
     **새 API 없음**
   - `frontend/src/ProjectDetail.tsx`: `SubmissionReview.tsx` 의 `DemoPanel`·`GithubPanel`·
     `CommitSummaryLine` 을 export 해서 재사용. 심사 보조 분석 패널은 붙이지 않는다(비공개)
   - `App.tsx`: 대회 목록 클라이언트 검색(이름 포함 검색, 백엔드 변경 없음)
   - `ContestDetail.tsx`: 스코어보드 히어로 유지, "제출물 둘러보기" 진입 링크 추가
4. **스타일:** `frontend/src/style.css` 에 토큰 추가 없이 기존 토큰으로 갤러리·상세 규칙 작성.

**A 의 계약:** A 에서 새로 쓰는 데이터 호출도 전부 `api.ts` 함수를 통한다. 컴포넌트 안에서
`fetch` 를 직접 부르지 않는다.

---

## B. Supabase 백엔드 (주말 2)

로컬: Supabase CLI `supabase init` → `supabase start`(Docker, 기본 포트 54321~54324.
Windows 예약 포트 대역에 걸리지 않음을 먼저 확인: `netsh int ipv4 show excludedportrange protocol=tcp`).
마이그레이션은 `supabase/migrations/*.sql` 로 관리한다.

### 테이블

`contests, teams, participants, submissions, judges, scores, awards, profiles, tech_stacks,
submission_reviews, github_cache`. 사용자는 `auth.users` + `profiles(id = auth.uid, username,
is_staff, …기존 Profile 필드)`. 컬럼·제약은 `backend/contests/models.py` 를 그대로 옮긴다
(예: `scores` 의 `unique (submission_id, judge_id, round)`, `value numeric(5,2) check 0~100`).
`tech_stacks` 시드는 `backend/contests/tech_stacks.py` 의 `SEED` 120건을 SQL 로 변환.

### Django 규칙 → Supabase 매핑

| 규칙 (지금 위치) | Supabase 구현 |
|---|---|
| 운영자 판별 `is_staff` | `is_organizer()` SECURITY DEFINER 헬퍼, RLS 에서 사용 |
| 상태별 허용 동작 `*_STATUSES` (views.py) | BEFORE 트리거가 한국어 메시지로 예외 발생("… (현재 상태: 심사중)"). RLS 만으로는 메시지를 못 줘서 트리거로 한다 |
| 점수 upsert + 동시 저장 안전 (ScoreViewSet) | RPC `submit_score()`: `insert … on conflict … do update`. 심사위원 배정·심사중 확인 포함 |
| 채점 이력 있는 심사위원 해제 금지 | `judges` BEFORE DELETE 트리거 |
| 스코어보드: 평균·동점 공동순위(1,1,3)·미채점 맨 아래·결선 비공개 | RPC `scoreboard(slug)`: `rank()` 윈도 함수, 호출자가 운영자·해당 대회 심사위원이 아니면 결선 제외 |
| 발표 시작 시 다른 팀 자동 종료 / 종료 / 되돌리기 | RPC `start_presentation`, `end_presentation`, `reset_presentation` (한 트랜잭션) |
| 발표 순서 일괄 배정 | RPC `assign_presentation_order(slug)` |
| 시상 정보 운영자 전용(읽기 포함) | `awards` RLS select 도 `is_organizer()` |
| 점수 목록: 운영자 전체 / 그 외 본인 것만 | `scores` RLS |
| 심사 보조 분석: 운영자·배정 심사위원만 | `submission_reviews` RLS |
| 프로필 skills 는 활성 정규 스택만 | `profiles` 트리거가 `tech_stacks` 대조 |
| 대회 삭제 시 하위 데이터 삭제 | FK `on delete cascade` 그대로 |

### 실시간

`scores`·`teams`·`submissions` 변경 트리거에서
`realtime.send(jsonb_build_object('slug', slug), 'scoreboard_changed', 'contest:' || slug, false)`.
**페이로드에 점수를 싣지 않는다.** 공개 채널이라 누구나 받으므로, 신호만 보내고 화면이
`scoreboard()` RPC 를 다시 부른다. 결선 비공개 규칙은 RPC 가 지킨다.
5초 폴링·ETag·지터·`visibilitychange` 코드(`ContestDetail.tsx`)는 C 에서 이 구독으로 바꾼다.

### 인증

- 로그인 식별자를 **아이디 → 이메일**로 바꾼다. 아이디로 로그인하려면 아이디로 이메일을 찾아 주는
  함수가 필요한데, 그 함수 자체가 남의 이메일을 알려주는 통로가 된다. 아이디는 `profiles.username`
  으로 표시용만 남긴다
- 기존 회원: Django PBKDF2 해시를 가져올 수 없어 **비밀번호 재설정 메일**로 옮긴다.
  Supabase 기본 메일 발송은 시간당 발송량 제한이 빡빡하니, 회원 수를 먼저 세고 많으면 커스텀 SMTP 설정

### 테스트 (pgTAP, `supabase test db`)

`backend/contests/tests.py` 의 테스트 이름을 **명세 목록**으로 쓴다. 옮길 것과 버릴 것:

- 옮김: StatusGating 11, ScoreboardRanking 5, ScoreboardPrivacy 4, Scoreboard 6, Presentation 14,
  MeAndJudgeAssignment 12, ContestStatusTransition 7, ContestApi 7, ContestDelete 5, Award 5,
  Profile 15, TechStack 9, AuthFlow 4 (Auth 로 바뀌는 부분은 새로 씀)
- 버림: QueryCount 5, ScoreboardCaching 5, ScoreboardCors 3. Django·캐시 구조를 검증하던 테스트라
  대상이 사라진다
- C 에서 Deno 테스트로: JudgeAssist 13, JudgeAssistRepoCollection 2, GithubProxy 9,
  GithubCommitSummary 5, TeamMatching 9

---

### B 진행 기록 (2026-09-15, 완료)

- 로컬: `npx supabase start -x imgproxy,logflare,vector,supavisor,edge-runtime`, 테스트는 `npx supabase test db`
  (pgTAP 4개 파일, 112건 통과), `npx supabase db lint` 오류 없음. 스토리지는 쓰지 않아 `config.toml` 에서 껐다.
- 계획보다 늘어난 것:
  - **조회용 뷰** `contest_list`·`team_list`·`judge_list`·`score_list`. Django 응답 모양(예: 팀의
    participants 에 username, `presentation_due_at`)을 DB 에서 그대로 만들어, C 에서 `api.ts` 가 모양을
    다시 조립하지 않게 했다.
  - RPC `assign_judge`(아이디로 배정)·`set_team_schedule`(발표 순서·시간은 운영자만). 팀원이 팀을 고칠
    권한이 있어도 이 열은 못 건드리게 열 단위 권한으로 막았다.
  - 태그 필드는 JSON 대신 `text[]`.
- 실시간 규칙: 토픽 `contest:{slug}`, 이벤트 `changed`, 페이로드는 바뀐 테이블 이름뿐(점수 없음).
  대회·팀·참가자·제출물·점수가 바뀔 때 보낸다.
- supabase-js 로 끝까지 확인한 것: 가입 시 프로필 생성, 이메일 로그인, anon 키로 `awards`·`scores`·
  `submission_reviews`·`github_cache`·`profiles` 조회 0건, 비로그인 구독자의 신호 수신, 상태 제한 메시지가
  REST 오류로 그대로 전달(code 42501).
- **C 에서 반영할 것:** 신호는 구독이 붙은 뒤에 쓴 변경만 온다(로컬에서는 첫 구독 때 복제 스트림이 열리는
  사이의 변경을 실제로 놓쳤다). 화면은 `SUBSCRIBED` 직후 한 번 다시 불러와야 한다.

## C. Edge Functions + 프론트 데이터 계층 (주말 3)

### Edge Functions (`supabase/functions/`)

| 함수 | 옮겨 올 코드 | 주의 |
|---|---|---|
| `_shared/llm.ts` | `llm/base.py` 3사 어댑터, `parse_json_object` | 프롬프트 문자열은 **그대로 복사**(모델 비교 조건 유지). 키는 `supabase secrets set` |
| `profile-extract` | `profile_extract.py`, 스택 별칭 매핑 `tech_stacks.resolve` | 30초 제한 유지 |
| `github` | `github.py` 프록시 5종 + 첫 커밋 | LocMemCache 대신 `github_cache` 테이블(만료 시각). `GITHUB_TOKEN` 필수로 둔다 |
| `analyze-submission` | `judge_assist.py` | 벽시계 **150초**. Django 에서 `pallets/flask` 가 GitHub 순차 조회로 8분 걸렸으니 파일 조회를 병렬로 바꾸고 상한 유지. 응답은 즉시, 분석은 `EdgeRuntime.waitUntil`. 일괄 분석은 제출물마다 이 함수를 따로 호출(한 호출에 몰지 않음) |
| `recommendations` | `matching.py` (가중치 45/25/20/10) | 남의 프로필 전체를 클라이언트에 주지 않으려고 서버 쪽에 둔다 |

### 프론트

- `frontend/src/api.ts`: **내보내는 함수 이름·타입은 유지**, 내부만 supabase-js 로 교체.
  리프레시 토큰 처리·`conditionalGet`·ETag 캐시는 삭제(supabase-js 세션이 담당).
  `AUTH_EXPIRED_EVENT` 는 `onAuthStateChange` 로 발생
- `github.ts`: Edge Function `github` 호출로 교체
- `ContestDetail.tsx`: 폴링 루프 → Realtime 구독 훅. "LIVE · 5초마다 갱신" 문구도 수정
- `AuthPanel.tsx`: 이메일 로그인 + 비밀번호 재설정 링크
- Vercel 환경변수: `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` (`VITE_API_BASE_URL` 제거)

---

### C 진행 기록 (2026-09-15, 완료)

- Edge Functions 5개(`supabase/functions/`): `llm-models`, `profile-extract`, `github`,
  `recommendations`, `analyze-submission`. 외부 호출 없는 규칙은 `_shared/logic.ts` 에 모았고
  `npx deno test supabase/functions/_shared/logic_test.ts` 8건 통과.
- 로컬 실행: `npx supabase start -x imgproxy,logflare,vector,supavisor` 후
  `npx supabase functions serve --env-file supabase/functions/.env`(LLM 키, gitignore 대상).
  `start` 가 띄우는 edge runtime 은 기동 뒤에 만든 함수를 못 찾아 `functions serve` 로 돌렸다.
- 계획과 달라진 것:
  - LLM 3사는 SDK 없이 HTTP 로 부른다(Deno 에서 의존성 없이).
  - 로그인은 이메일, 가입 때 아이디를 함께 받는다. 비밀번호 재설정 메일과 새 비밀번호 화면을 넣었다.
  - Render 콜드스타트용 "서버 깨우는 중" 안내를 지웠다.
  - 번들이 206KB → 436KB(gzip 65KB → 125KB)로 늘었다. supabase-js 몫이다.
- 확인한 것:
  - 브라우저(비로그인): 목록·상세·갤러리가 Supabase 데이터로 뜨고, DB 에 점수를 넣자 새로고침 없이
    2초 안에 스코어보드 순위가 바뀌었다.
  - supabase-js 로 api.ts 와 같은 쿼리를 역할별로 27건(대회·팀·제출물·발표·채점·시상·프로필·
    분석·추천): 전부 통과.
  - `analyze-submission` 실제 실행: `pallets/itsdangerous` 파일 23개, 입력 17.5k 토큰으로 `done`.
- 확인 못 한 것: 로그인한 상태의 화면 조작(운영자·심사위원 화면)은 사람이 직접 눌러 봐야 한다.

## D. 데이터 이전과 전환

1. 공지 후 쓰기 중단. 기존 백업 워크플로(`.github/workflows/supabase-backup.yml`) 수동 실행으로 백업 확보
2. 일회성 변환 스크립트: Django 테이블(`contests_*`, `auth_user`) → 새 테이블. 사용자는 Auth admin
   API 로 생성 후 재설정 메일
3. 건수 대조(대회·팀·제출물·점수·프로필)
4. Vercel 배포 → 운영자·심사위원·참가자·비로그인 4개 역할로 스모크 테스트
5. **Render 는 1주 동안 끄지 않고 둔다**(롤백 창). 문제 없으면 Render 서비스 삭제, Django 테이블은
   백업 후 삭제, `backend/` 디렉터리 제거
6. 문서: `DEVELOPMENT.md` 아키텍처 드라이버 표 재작성(워커 1개 제약 소멸, 새 제약은 Edge 150초·
   CPU 2초·RLS), 배포 절차, 트러블슈팅. `README.md` 로그인 안내. keepalive 워크플로는 DB 직접 접속이라 그대로 동작

### D 진행 기록 (2026-09-15, 리허설 완료 · 운영 전환 대기)

- **보안 구멍 발견·수정:** Django 테이블(`auth_user`, `contests_*`)이 새 스키마와 같은 public 에 있고
  RLS 꺼짐 + anon 전체 권한이라, anon 키로 REST 를 부르면 비밀번호 해시를 읽고 행을 지울 수 있었다
  (로컬에서 `auth_user?select=password` 로 해시 확인). 프론트가 anon 키를 번들에 싣는 순간 운영도 같다.
  `20260915000200_lock_django_tables.sql` 로 RLS 켜고 anon·authenticated 권한 회수. 이제 42501.
- **변환 스크립트는 SQL 한 파일:** `supabase/scripts/migrate-from-django.sql`. 같은 DB 라 Node·키 없이
  psql 로 한 트랜잭션에 옮긴다. 계획의 "Auth admin API 로 생성" 대신 `auth.users`·`auth.identities` 에
  직접 넣었다(토큰 컬럼은 NULL 아닌 '' — NULL 이면 GoTrue 로그인이 깨진다). Django id 는
  `overriding system value` 로 유지해 매핑 표가 필요 없다. 상태 제한 트리거는 트랜잭션 동안만
  `disable trigger user`(FK·check 는 계속 검사). 기본은 미리보기(롤백), `-v apply=1` 일 때만 커밋.
  이메일 없는 계정·겹치는 이메일이 있으면 시작 전에 멈춘다. 건수가 하나라도 다르면 되돌린다.
- **재설정 메일을 일괄 발송하지 않는다.** 공지로 "로그인 화면의 비밀번호를 잊으셨나요?"를 안내한다.
  발송 제한에 덜 걸리고, 운영자가 대신 메일을 뿌릴 필요가 없다.
- **리허설(로컬, 도커의 Django DB 사본):** 이메일 없는 계정에서 멈춤 확인 → 이메일 채운 뒤 11개 항목
  건수 일치 → 반영. 이어서 확인: 빈 비밀번호 로그인 실패, 재설정 메일 Mailpit 도착, 새 비밀번호로
  로그인·username 유지, 프로필 skills 이전, 새 팀 id=6(시퀀스), join_creator·상태 제한 트리거 재가동,
  anon 의 `auth_user` 조회 거부. pgTAP 112건 통과.
- 운영에서 알게 된 제약: Supabase 기본 메일은 **프로젝트 팀원 주소로만, 시간당 2통**. 운영 전환 전에
  커스텀 SMTP 필수.

### 운영 전환 순서 (체크리스트)

지금 당장 해도 되는 것(Django 무영향):
1. ~~`npx supabase login` → `link --project-ref` → `db push`~~ **완료 (2026-09-16)**.
   프로젝트 `ugrooqkeyhgldrtdriba`(ap-southeast-1)에 마이그레이션 3개 반영.
   anon 키 확인 결과 `auth_user`·`contests_*`·`django_*` 전부 `42501 permission denied`(읽기·쓰기 모두),
   새 테이블 `contests`·`tech_stacks` 는 200, `awards`·`scores`·`submission_reviews` 는 RLS 로 빈 배열.
   Django(`/api/contests/`)는 그대로 동작한다.
2. ~~대시보드 Auth: SMTP 설정~~ **완료 (2026-09-16)**.
   Resend + 발신 주소 `onboarding@resend.dev`(도메인 미보유라 임시. 스팸함으로 갈 수 있음 — 정식
   도메인 생기면 발신 주소만 교체). Site URL·Redirect URL 을 `https://hackman-sju.vercel.app` 로 설정.
   `auth/v1/invite` 로 실제 수신 확인(초대 수락·비밀번호 설정까지 완료 후 테스트 계정은 삭제).
3. ~~시크릿 등록 → `functions deploy`~~ **완료 (2026-09-16)**.
   등록한 시크릿은 `GOOGLE_API_KEY`·`GITHUB_TOKEN` 둘뿐이다. `ANTHROPIC_API_KEY`·`OPENAI_API_KEY`
   는 쓰지 않기로 해서 넣지 않았다. `_shared/llm.ts` 의 `keyOf` 가 키 있는 제공사만 노출하므로
   **모델 선택기에는 Google 만 뜬다**(Render 에서는 3사 전부 떴다). 나중에 되살리려면 키를 등록하고
   `functions deploy` 를 다시 돌리면 된다. 자리표시자 값을 넣으면 "키 있음"으로 잘못 판단하니 주의.
   `SUPABASE_URL`·`SUPABASE_SERVICE_ROLE_KEY` 는 런타임이 자동으로 넣으므로 등록하지 않는다.
   함수 5개(`analyze-submission`·`github`·`llm-models`·`profile-extract`·`recommendations`)
   전부 배포됐고, anon 키로 호출하면 `401 로그인이 필요합니다` 가 나온다(부팅·인증 가드 정상).

전환 당일(약 1시간) — **완료 (2026-09-16~17)**:
4. ~~공지 후 쓰기 중단, 백업~~ **의도적으로 생략**. 실사용자가 없는 테스트 단계라 사용자
   판단으로 건너뜀(공지 대상 없음, 백업은 이미 로컬 리허설로 대체 확인됨).
5. Django admin 에서 이메일 정리 — 실제로는 계정이 `admin`(스태프) 하나뿐이었고 나머지 2개
   (`gustjr3332`, `keycheck-*`)는 테스트 계정이라 이메일을 채우는 대신 **삭제**(딸린
   `contests_judge`·`contests_profile` 행도 함께 정리). `admin` 이메일은 실사용 주소로 교체.
6. ~~psql 미리보기 → apply~~ **완료**. 로컬 Docker 의 psql 로 프로덕션에 접속해 미리보기 →
   11/11 건수 일치 확인 → `apply=1` 로 반영, `COMMIT`.
7. ~~Vercel 환경변수 → main 병합~~ **완료**. `overhaul/devpost-supabase` 를 `main` 에 fast-forward
   병합, push. Vercel 자동 배포 확인(`200`, 새 빌드).
8. 스모크 테스트 — 비로그인 경로(목록·상세·갤러리·프로젝트 상세·결선 비공개)는
   claude-in-chrome 으로 확인, 콘솔 에러 0. **로그인이 필요한 역할별 확인은 비밀번호를 다루는
   구간이라 사용자가 직접** 진행(`docs/smoke-test.md` 체크리스트 참고). admin 아이디 로그인
   확인 완료. 공지는 4번과 같은 이유로 생략.

전환 후 곁들여 고친 것(원래 계획엔 없던 사용자 요청):
- 로그인 화면과 대회 목록 분리 — 기본 화면은 목록, 로그인은 헤더 "로그인" 버튼으로만 연다
- 아이디 로그인 허용 — 새 Edge Function `login`. 아이디→이메일 조회는 service_role 로 서버
  안에서만 하고 클라이언트로 이메일을 절대 넘기지 않는다. 존재하지 않는 아이디와 틀린 비밀번호가
  같은 오류를 내 계정 존재 여부가 새지 않게 함
- 푸터의 기술 스택 안내 줄 삭제
- 데모 링크가 GitHub 저장소·`localhost`·사설 IP 대역이면 흰 화면 대신 안내 문구로 대체
- 배포마다 반복할 스모크 테스트 체크리스트 추가(`docs/smoke-test.md`)

1주 뒤(2026-09-23 경, 아직 안 함): Render 서비스 삭제 → Django 테이블 백업 후 drop →
`backend/` 제거 → `DEVELOPMENT.md`·`README.md` 최종 정리(이번 전환으로 두 문서 일부 갱신은
먼저 반영해 둠, 전체 재구성은 나중에 한 번에)

---

## 위험과 미결

| 항목 | 내용 | 대응 |
|---|---|---|
| 재작성 규모 | 백엔드 약 4,500줄(테스트 포함), 테스트 155건 | 명세는 tests.py 이름 목록, 단계별 통과 조건으로 끊는다 |
| 로그인 방식 변경 | 아이디 → 이메일 | 전환 공지에 포함 |
| 기존 회원 재설정 | PBKDF2 해시 이전 불가 | 회원 수 확인 후 SMTP 결정 |
| Edge 150초 | 큰 저장소 분석 실패 가능 | 병렬 조회, 파일·문자 상한, 실패는 상태로 기록(지금과 같음) |
| Supabase 7일 무활동 정지 | 지금도 있는 위험 | 기존 keepalive 워크플로 유지 |
| Claude 폴더 정리 | 계획·스킬 링크 유실 가능 | 0단계 1·2번 |

---

## 검증

- **0단계:** archify `validate --quality showcase` 가 9개 검사 통과·경고 0, `deliver` 성공,
  `visual-check` 수치 확인. as-is 도면의 모든 노드가 코드 근거(파일 경로)와 맞는지 대조
- **A:** `npm run build` 통과. 로컬에서 4개 경로를 라이트/다크, 400px 폭, 비로그인·참가자·운영자로
  확인(claude-in-chrome). 비로그인 사용자에게 갤러리 결선 점수·분석이 보이지 않는지 확인.
  기존 테스트 155건 그대로 통과(백엔드 무변경)
- **B:** `supabase test db` 전부 통과. 비로그인 anon 키로 `awards`·`scores`·`submission_reviews`
  직접 조회가 막히는지 확인
- **C:** Edge Function Deno 테스트 통과. `analyze-submission` 을 `pallets/itsdangerous` 로 실제
  실행해 150초 안에 `done`(Django 기준 27초). 두 브라우저로 점수 저장 → 다른 쪽 스코어보드가
  폴링 없이 갱신되는지 확인
- **D:** 건수 대조 일치, 4개 역할 스모크 테스트, Render 중지 상태에서 서비스 전체 동작
