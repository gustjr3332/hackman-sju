# HACKMAN 개발 문서

개발·배포·운영자를 위한 문서다. 서비스 소개와 사용 방법은 [README.md](README.md), 화면 디자인
기준은 [DESIGN.md](DESIGN.md)를 본다. 여기에는 도메인 모델, 저장소 구조, 기술 스택, 로컬 개발 환경,
테스트, 배포, 로드맵, 트러블슈팅 기록을 둔다.

- 백엔드: https://web-claude-t.onrender.com/api/contests/
- 프론트엔드: https://hackman-sju.vercel.app/

Django REST Framework + React(Vite) 기반 해커톤/공모전 운영 플랫폼입니다. 대회 생성 →
팀 구성 → 제출물 등록 → 심사위원 채점 → 실시간 스코어보드로 이어지는 흐름을 지원합니다.
우아한형제들 해커톤 운영 사례(예선 15분·결선 10분 실시간 집계)를 참고 모델로 삼았습니다.
향후 Flutter로 동일 Django REST API를 재사용하는 웹+앱 하이브리드 확장을 계획하고 있습니다.

## 도메인 모델

`Contest` — `Team` — `Participant` / `Submission` — `Judge` — `Score`

- 대회 상태 전이: 모집중 → 진행중 → 심사중 → 종료 (운영자가 대회 상세 화면에서 전환)
- 역할: 운영자(staff) / 참가자 / 심사위원
- 채점: 팀의 제출물 1건에 대해 심사위원별로 예선/결선 라운드 점수·코멘트 입력.
  같은 심사위원이 같은 라운드에 다시 저장하면 기존 점수를 덮어쓴다(upsert).
- 스코어보드: 라운드별 평균 점수·심사 수·**순위**를 집계. 동점은 같은 순위를 공유하고 다음
  순위는 건너뛴다(1, 1, 3). 점수가 없는 팀은 순위 없이 맨 아래에 표시. `preliminary`(예선,
  코드/기능 점수)는 항상 공개, `final`(결선, 발표 점수 포함 종합 점수)은 운영자·배정된
  심사위원에게만 공개(시상 전까지 비공개).
- 발표 일정: `Contest.presentation_start_at`/`presentation_minutes` + `Team.presentation_order`
  로 팀별 발표 시작/종료 시각을 계산(저장하지 않고 매번 계산). 운영자만 배정 가능.
- 시상: `Award`(대회, 등수, 상 이름)를 운영자가 미리 등록해 두고 시상식에서 등수별 최종
  순위와 매칭해 순서대로 공개. `Award`는 읽기 포함 운영자 전용.

대회 상태에 따라 서버가 허용하는 동작 (프론트는 같은 규칙으로 폼을 숨기고, 강제는 서버가 함):

| 동작 | 모집중 | 진행중 | 심사중 | 종료 |
|---|:-:|:-:|:-:|:-:|
| 팀 생성 / 참가 | O | O | – | – |
| 제출물 등록 / 수정 | O | O | – | – |
| 심사위원 채점 | – | – | O | – |
| 스코어보드 조회 | O | O | O | O |

허용되지 않는 상태에서 요청하면 `403` + `"… (현재 상태: 심사중)"` 형태의 메시지를 돌려준다.
규칙 정의: `backend/contests/views.py`의 `*_STATUSES`, `frontend/src/rules.ts`.

## 저장소 구조

```
.
├── backend/                # 백엔드: Django + DRF + PostgreSQL
│   ├── config/              # 프로젝트 설정 (settings.py, urls.py, wsgi.py)
│   ├── contests/             # 대회/팀/제출물/심사 도메인 앱 (models, serializers,
│   │                           permissions, views, migrations)
│   ├── postman/               # Postman 컬렉션/환경 (엔드포인트 수동 검증용)
│   ├── docker-compose.yml       # 로컬 PostgreSQL 컨테이너
│   ├── Procfile                  # 배포 시작 명령 (Render)
│   └── requirements.txt
├── frontend/                # 프론트엔드: React + TypeScript + Vite
│   └── src/                    # App.tsx, AuthPanel.tsx, ContestForm.tsx, ContestDetail.tsx,
│                                 api.ts, types.ts, labels.ts, rules.ts, style.css
├── .devcontainer/            # Python+Node+PostgreSQL 개발 컨테이너 (VS Code Dev Containers)
├── README.md                # 서비스 소개·사용 방법 (운영자/참가자/심사위원 대상)
├── DEVELOPMENT.md            # 이 문서: 개발·배포·운영 기록
└── DESIGN.md                 # 프론트엔드 디자인 기준
```

## 기술 스택

| 영역 | 선택 | 비고 |
|---|---|---|
| 백엔드 | Python / Django 6.1 + Django REST Framework | REST API 서버, 프론트와 완전히 분리 |
| 프론트엔드 | TypeScript / React 18 (Vite) | SPA, 백엔드 API를 fetch로 호출 |
| 인증 | JWT (`djangorestframework-simplejwt`) | 웹+앱(Flutter) 공용 전제 |
| DB | PostgreSQL 16 | 로컬 개발은 Docker, 배포는 Render 관리형 DB |
| API 테스트 | Postman | `backend/postman/`에 컬렉션·환경 파일로 관리 |
| 배포(백엔드) | Render (Web Service + 관리형 PostgreSQL) | gunicorn + whitenoise |
| 배포(프론트) | Vercel | Root Directory: `frontend` |
| 향후 하이브리드 앱 | Flutter | 같은 Django REST API 재사용 예정 |

## 로컬 개발 환경

### 백엔드

```bash
cd backend
docker compose up -d          # PostgreSQL 컨테이너 기동 (localhost:5432)
python -m venv .venv && .venv/Scripts/activate   # (Windows) 최초 1회
pip install -r requirements.txt
cp .env.example .env          # 필요 시 값 수정
python manage.py migrate
python manage.py runserver    # http://127.0.0.1:8000
```

`.env`가 없으면 Django가 기본값(`webclaude`/`webclaude`)으로 로컬 Postgres에 접속합니다.
`DATABASE_URL` 환경변수가 설정되어 있으면 `POSTGRES_*` 값 대신 그걸 우선 사용합니다
(Render 등 PaaS 배포용).

Docker를 띄우지 않고 빠르게 돌려볼 때는 SQLite를 지정하면 됩니다:

```powershell
# PowerShell
$env:DATABASE_URL = "sqlite:///$PWD/dev.sqlite3"; python manage.py migrate; python manage.py runserver
```

```bash
# bash
DATABASE_URL="sqlite:///$(pwd)/dev.sqlite3" python manage.py migrate && DATABASE_URL="sqlite:///$(pwd)/dev.sqlite3" python manage.py runserver
```

### 백엔드 테스트

`backend/contests/tests.py`에 API 테스트 47건(인증·토큰 갱신, 대회 CRUD·상태 전이, 상태별
동작 제한, 팀/제출물 권한, 심사위원 배정, 스코어보드 순위 집계, 쿼리 수 고정 검증)이 있습니다.
Postgres가 없어도 SQLite로 실행됩니다:

```powershell
# PowerShell
$env:DATABASE_URL = "sqlite:///$PWD/test.sqlite3"; python manage.py test
```

```bash
# bash
DATABASE_URL="sqlite:///$(pwd)/test.sqlite3" python manage.py test
```

### 프론트엔드

```bash
cd frontend
npm install
cp .env.example .env          # VITE_API_BASE_URL 확인 (기본: http://127.0.0.1:8000/api)
npm run dev                   # http://localhost:5173
npm run build                 # tsc 타입 검사 + 프로덕션 번들 (배포 전 확인용)
```

프론트 동작 메모:

- 로그인 시 access/refresh 토큰을 모두 `localStorage`에 저장하고, API가 `401`을 돌려주면
  refresh 토큰으로 한 번 재발급한 뒤 원 요청을 재시도합니다(동시 요청은 재발급 1회로 합침).
  재발급도 실패하면 로그아웃 처리 후 "로그인이 만료되었습니다" 안내가 뜹니다.
- 대회 상세 화면은 **5초마다** 팀 목록·스코어보드·대회 상태를 다시 가져옵니다(탭이 백그라운드면
  건너뛰고, 다시 보이면 즉시 갱신). 운영자가 상태를 바꾸면 참가자·심사위원 화면도 다음 폴링에서
  폼 잠금/해제가 따라갑니다. 종료된 대회는 30초 주기로만 확인합니다. 상단
  `LIVE · 5초마다 갱신 · hh:mm:ss` 표시로 마지막 갱신 시각을 확인할 수 있고, 요청이 실패하면
  `연결 끊김 · 재시도 중`으로 바뀝니다.
- 운영자(staff) 계정은 목록 화면에서 **새 대회 만들기**, 상세 화면에서 **상태 전이**
  (모집중 → 진행중 → 심사중 → 종료)와 심사위원 배정을 할 수 있습니다.

### API 엔드포인트 검증

`backend/postman/WebClaude.postman_collection.json` + `WebClaude.postman_environment.json`을
Postman에 가져오면 회원가입/로그인(JWT), 대회 CRUD, 팀 생성/참가, 제출물 등록/수정,
심사 점수 입력, 스코어보드 조회 요청을 바로 실행해볼 수 있습니다.
로컬 대상: `base_url = http://127.0.0.1:8000/api`.

### 가입 계정 / 운영자(superuser) 확인

가입된 사용자 목록과 운영자 여부(`is_staff`, `is_superuser`)는 별도 API가 없고 아래 세 가지
방법으로 확인합니다.

**1. Django admin** — https://web-claude-t.onrender.com/admin/ → *인증 및 권한 › 사용자*.
superuser 계정 하나가 있어야 로그인할 수 있고, 여기서 다른 계정에 `is_staff`를 켜면 그 계정이
바로 운영자(대회 생성·상태 전이·심사위원 배정 가능)가 됩니다. 앱에서 로그인한 뒤
`GET /api/auth/me/`로 자기 자신의 `is_staff`만 확인할 수도 있습니다.

**2. 로컬 PC에서 Render DB에 직접 연결** — Render Shell(유료 플랜 전용) 없이 됩니다.
Render 대시보드 → PostgreSQL 서비스 → *Info* → **External Database URL**을 복사해서, 로컬
`backend/` 디렉터리에서 그 값을 `DATABASE_URL`로 넘겨 manage.py 를 실행하면 프로덕션 DB를
대상으로 동작합니다:

```powershell
# PowerShell (backend/ 에서, .venv 활성화 상태)
$env:DATABASE_URL = "postgres://...external url..."
python manage.py shell -c "from django.contrib.auth.models import User; [print(u.id, u.username, u.email, u.is_staff, u.is_superuser) for u in User.objects.all()]"
python manage.py createsuperuser        # superuser가 하나도 없을 때
```

```bash
# bash
DATABASE_URL="postgres://...external url..." python manage.py shell -c "
from django.contrib.auth.models import User
for u in User.objects.all():
    print(u.id, u.username, u.email, 'staff' if u.is_staff else '', 'superuser' if u.is_superuser else '')
"
```

이미 있는 계정을 운영자로 올릴 때는
`User.objects.filter(username='아이디').update(is_staff=True)` 한 줄이면 됩니다.
External URL은 외부 접속용이라 Render 내부 URL과 다르고, 무료 DB는 만료 시 URL이 바뀝니다.

**3. DB 클라이언트** — 같은 External Database URL을 DBeaver / TablePlus / psql에 넣고
`SELECT id, username, email, is_staff, is_superuser FROM auth_user;`.

로컬 개발 DB에서는 그냥 `python manage.py createsuperuser` 후 http://127.0.0.1:8000/admin/ 입니다.

## 배포

### 백엔드 (Render)

- URL: https://web-claude-t.onrender.com
- Root Directory: `backend`
- Build: `pip install -r requirements.txt`
- Start: `python manage.py migrate --noinput && python manage.py collectstatic --noinput && gunicorn config.wsgi`
  (대시보드 **Start Command** 필드가 `Procfile`보다 우선이므로 둘을 같은 값으로 유지)
- DB: Render 관리형 PostgreSQL, `DATABASE_URL`로 연결
- 환경변수: `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=False`,
  `DJANGO_ALLOWED_HOSTS=web-claude-t.onrender.com`, `DATABASE_URL`,
  `CORS_ALLOWED_ORIGINS=https://hackman-sju.vercel.app`

### 프론트엔드 (Vercel)

- URL: https://hackman-sju.vercel.app
- Root Directory: `frontend`
- 환경변수: `VITE_API_BASE_URL=https://web-claude-t.onrender.com/api`
  (Vite는 빌드 시점에 env를 박아 넣으므로, 값 변경 후 반드시 재배포 필요)

### 커스텀 도메인 (예정, 2026-09-03 조사)

비용은 도메인 등록비만 든다. Vercel·Render의 커스텀 도메인 연결과 SSL(Let's Encrypt 자동
발급·갱신)은 둘 다 무료 플랜에 포함.

| 항목 | 비용 | 비고 |
|---|---|---|
| `.com` | 연 $10~13 | Cloudflare Registrar·Porkbun이 원가 판매 |
| `.kr` / `.co.kr` | 연 약 2.2만원 | 가비아·후이즈 |
| `.xyz` 등 | 첫해 $2~5 | 갱신비 확인 필요 |
| Vercel / Render 연결, SSL | 무료 | |

**추천 구성: 프론트만 도메인 연결.** 참가자가 보는 주소만 바뀌면 되고 백엔드는
`web-claude-t.onrender.com`을 그대로 써도 된다. 절차 (약 20분 + DNS 전파):

1. 도메인 구매 (예: `sjuhack.com`)
2. Vercel → 프로젝트 → Settings → Domains → 도메인 추가. 안내대로 DNS에 A 레코드
   (`76.76.21.21`) 또는 CNAME(`cname.vercel-dns.com`) 등록. SSL 자동
3. Render → web-claude-t → Environment → `CORS_ALLOWED_ORIGINS`에
   `https://sjuhack.com,https://www.sjuhack.com` 추가 (기존 vercel 주소는 유지). 저장 시 자동 재배포
4. 새 주소로 로그인 한 번 해서 확인

백엔드까지 `api.sjuhack.com`으로 붙일 경우 추가 작업: Render Custom Domains에 등록(DNS CNAME →
`web-claude-t.onrender.com`), `DJANGO_ALLOWED_HOSTS`에 추가, Vercel
`VITE_API_BASE_URL=https://api.sjuhack.com/api`로 변경 후 **재배포**(Vite는 빌드 시 값이 박힘).

도메인보다 먼저 볼 비용: Render Free는 15분 미사용 시 잠들어 첫 요청에 30~50초 걸린다. 대회
당일 체감이 크므로 대회 기간만 Starter 플랜(월 $7)으로 올려 상시 가동하는 방안을 검토.

## 로드맵

### 완료

- Django + DRF + PostgreSQL 세팅 (로컬 Docker)
- 도메인 모델(`Contest`/`Team`/`Participant`/`Submission`/`Judge`/`Score`) + 마이그레이션
- JWT 인증 + 역할 기반 권한(운영자/참가자/심사위원)
- 핵심 API: 대회 CRUD, 팀 생성/참가, 제출물 등록/수정, 심사 점수 입력, 스코어보드 집계
- React 프론트: 로그인/회원가입, 대회 목록·상세, 팀 생성/참가, 제출물 등록, 스코어보드,
  **심사자 채점 화면**
- Postman 컬렉션 (회원가입~스코어보드 전 구간)
- Render 백엔드 배포 / Vercel 프론트엔드 배포
- 레거시 정적 사이트 + Google Apps Script 백엔드 제거 (2026-09-02)
- 프론트엔드 리디자인 — `DESIGN.md` 기준 헤어라인 리스트 + 히어로 스코어보드 (2026-09-03)
- **심사위원 배정 UI** — 대회 상세 화면에서 운영자(staff)가 아이디로 심사위원을 배정/해제
  (`GET /api/auth/me/`로 운영자 여부 확인, `POST/DELETE /api/judges/`) (2026-09-03)
- **대회 생성 / 상태 전이 UI** — 운영자가 목록 화면에서 대회를 만들고(slug 자동 제안,
  시작·종료 검증), 상세 화면에서 모집중 → 진행중 → 심사중 → 종료를 전환 (2026-09-03)
- **대회 상태 기반 동작 제한** — 팀 생성/참가·제출물 수정은 모집중/진행중에만, 채점은 심사중에만.
  서버가 `403`으로 강제하고 프론트는 같은 규칙으로 폼을 잠금 (2026-09-03)
- **스코어보드 실시간화(REST 폴링)** — 5초 주기 폴링 + LIVE 인디케이터, 예선/결선 라운드 탭,
  순위 컬럼(동점 공동 순위), 대회 상태 변경도 폴링으로 전파, 종료된 대회는 30초 주기 (2026-09-03)
- **JWT 액세스 토큰 자동 갱신** — `401` 시 refresh 토큰으로 재발급 후 재시도, 실패 시 세션
  만료 안내 (2026-09-03)
- 보안/안정성 수정 — 타 팀에 제출물 생성 가능했던 구멍 차단, 같은 라운드 중복 채점 시 500 →
  upsert, 대회 종료일 < 시작일 검증 (2026-09-03)
- 백엔드 API 테스트 37건 (SQLite로 Docker 없이 실행 가능) (2026-09-03)
- **`/code-review xhigh` 결과 반영 — 알고리즘/기능 연결성 최적화** (2026-09-03)
  - `ContestSerializer.team_count`가 대회마다 `teams.count()` 쿼리를 날리던 것을
    `ContestViewSet.get_queryset`의 `annotate(Count/Exists)`로 바꿔, 목록 조회가 대회 수와
    무관하게 고정 쿼리 수로 끝난다. 같은 annotate로 `is_judge`(요청자가 이 대회 심사위원인지)도
    함께 계산해 프론트가 더 이상 `judges` 배열을 문자열로 비교하지 않는다.
  - `TeamViewSet.get_queryset`이 참가자 `username`까지 `select_related`로 미리 가져와
    (기존엔 팀마다 참가자 목록 직렬화 시 유저 조회가 반복됨) 5초 폴링 부하를 줄였다.
  - **버그 수정** — 채점을 마친 심사위원을 해제하면 `Score.judge`가 `CASCADE`라 그 점수가
    통째로 사라지던 문제. `JudgeViewSet.perform_destroy`가 `score_count > 0`이면 403으로
    막고, 프론트는 해당 심사위원 옆에 채점 건수를 보여주며 해제 버튼을 비활성화한다.
  - **버그 수정** — 운영자가 자기 자신을 심사위원으로 겸임하면 `GET /scores/`가 전체 심사위원의
    점수를 돌려줘, 채점 화면이 남의 점수를 "내 점수"로 착각해 덮어쓸 수 있던 문제.
    `?mine=1` 쿼리로 항상 본인 점수만 받도록 프론트·백엔드를 맞췄다.
  - **버그 수정** — 로그인 폼에 입력한 문자열을 그대로 신원 비교에 썼던 것을, `/api/auth/me/`가
    돌려주는 서버 정식 아이디로 교체(앞뒤 공백 등으로 "내 팀"·심사 패널이 조용히 숨던 문제 해소).
    다른 탭에서 로그인/로그아웃하면 `storage` 이벤트로 이 탭도 같은 계정으로 맞춘다.
  - `JudgeSerializer`의 쓰기 전용 `user_username`/읽기 전용 `username` 이원화를 없애고
    `SlugRelatedField` 하나로 통일. 중복 배정 시 DRF 기본 영문 오류 대신 한국어 메시지를 준다.
    `JudgeViewSet`은 PUT/PATCH를 막아(`http_method_names`) 심사위원의 점수를 다른 사람 것으로
    재배정할 수 있던 경로를 닫았다.
  - 팀 참가(`join`) 응답이 방금 만든 `Participant`를 다시 조회하던 왕복을 없애고 바로 직렬화.
  - 회귀 테스트 10건 추가(쿼리 수 고정 검증 2건 포함, 총 47건), 프론트 `npm run build` 통과.

- **제출물 심사 도구 — 웹 데모 시현 + GitHub 코드 열람** (2026-09-04)
  범위: 웹 데모만 지원(앱 실행 테스트는 제외). 코드 리뷰는 GitHub 링크 기반 열람까지만
  (인라인 코멘트·diff 툴은 제외).
  - `Submission`에 `repo_url`(URLField, blank=True) 필드 추가, 마이그레이션 1개
    (`contests/migrations/0002_submission_repo_url.py`). 기존 `link_url`은 데모 URL
    용도로 그대로 유지. 제출물 폼에 "GitHub 저장소 URL (선택)" 입력 추가.
  - 심사 화면(`JudgePanel`)의 각 팀 카드에 "심사 도구 열기" 토글(`SubmissionReview.tsx`)을
    추가. 펼치기 전에는 아무 요청도 하지 않는다(GitHub API 요청량 절약).
  - **웹 데모 패널**: `link_url`을 iframe(`sandbox` 속성으로 팝업 탈출·최상위 탐색 등 제한)
    으로 띄우고 "새 탭에서 열기"를 항상 함께 노출(X-Frame-Options로 막히는지 JS로 감지할
    수 없어 fallback이 아니라 기본 노출).
  - **GitHub 코드 패널**: 백엔드 프록시 없이 프론트(`src/github.ts`)에서 `api.github.com`에
    직접 GET(공개 API, 비인증 60회/시간). `repo_url`에서 owner/repo 파싱 →
    `/repos/{owner}/{repo}` 로 기본 브랜치 조회 → `readme` + `git/trees?recursive=1`
    (블롭 500개 상한) 병렬 조회 → 파일 클릭 시 `/contents/{path}` → base64 디코드 후
    `<pre>`로 표시(문법 하이라이팅은 범위 밖). GitHub 저장소가 아닌 URL, 404(비공개/삭제),
    403/429(rate limit) 각각 "GitHub에서 직접 열기" 링크로 대체.
  - 백엔드 47건 테스트 통과(SQLite), `npm run build` 통과, `api.github.com`
    readme/trees/contents 응답 스키마를 실제 공개 저장소로 직접 검증. 브라우저 확장이
    연결되지 않아 화면 클릭 확인은 아직 못 했음 — 로컬에서 `npm run dev` 로 확인 필요.

- **다크 모드 / 라이트 모드** — 헤더 토글로 전환, `localStorage`에 저장해 다음 방문에도
  유지. 저장된 값이 없으면 시스템 설정(`prefers-color-scheme`)을 따른다. 첫 페인트 전에
  `index.html`의 인라인 스크립트가 저장된 테마를 적용해 깜빡임(FOUC) 없음. `style.css`의
  `#fff` 하드코딩 5곳을 `var(--surface)` 토큰으로 바꿔 입력창·스코어보드 hover·데모/코드
  패널까지 다크에서 깨지지 않게 함. (2026-09-05)
- **`/code-review` 결과 반영 — 보안/검증 버그 3건** (2026-09-05)
  - **버그 수정(보안)** — `TeamViewSet`에 객체 단위 권한 검사가 없어 로그인만 하면 아무 팀이나
    수정·삭제할 수 있던 구멍. 권한을 `IsAuthenticatedOrReadOnly` → `IsTeamMemberOrReadOnly`로
    교체(그 팀 참가자 또는 운영자만 쓰기 가능).
  - **버그 수정** — `Score.value`에 범위 검증이 없어 API로 직접 호출하면 음수·100 초과 점수가
    그대로 저장되던 문제. `MinValueValidator(0)`/`MaxValueValidator(100)` 추가
    (`migrations/0003_alter_score_value.py`).
  - 팀 이름 중복 시 DRF 기본 영문 메시지 대신 한국어 메시지(`이미 이 대회에 같은 이름의 팀이
    있습니다.`)를 주도록 `TeamSerializer`에 `UniqueTogetherValidator` 명시(다른 시리얼라이저와
    통일).
- **`/code-review` 결과 반영 — 대회 상태 전이 검증 + 채점 레이스 컨디션 수정** (2026-09-05)
  - **버그 수정** — 서버가 `recruiting → ongoing → judging → closed` 순서를 전혀 검사하지
    않아 운영자가 임의 상태로 바로 점프하거나 역행시킬 수 있던 문제.
    `ContestSerializer.ALLOWED_NEXT_STATUS`(인접 상태 맵)를 `validate()`에 추가해 제자리 이동
    또는 바로 다음 단계로만 전이를 허용. **상태를 바꿀 수 있는 사람은 그대로 운영자(staff,
    admin)뿐이다** — 이 검증은 `ContestViewSet.permission_classes = [IsOrganizerOrReadOnly]`
    (누가 바꾸는지)를 대체하지 않고, 그 위에 무엇으로 바꿀 수 있는지만 추가한 것. 프론트
    `StatusControl`(`ContestDetail.tsx`)도 현재 상태의 바로 다음 단계 버튼만 활성화하도록
    수정.
  - **버그 수정** — `ScoreViewSet.perform_create`의 upsert가 조회(`existing = ...`)와
    저장 사이의 간극 때문에 같은 심사위원이 탭 두 개로 거의 동시에 제출하면
    `unique_together` 위반 500이 날 수 있던 문제. `select_for_update()` + `transaction.atomic()`
    으로 기존 행이 있는 경우를 잠그고, 두 트랜잭션이 동시에 새 행을 만들려는 경우까지 막도록
    `IntegrityError`를 잡아 한 번 재시도(재조회 후 업데이트)하게 함.

- **대회 시간 운영 기능 4종 — 잔여시간 표시, 발표 일정, 종합 순위 비공개, 시상식 진행** (2026-09-05)
  실시간 해커톤 당일 운영(제출 마감 → 발표 → 시상)을 지원하기 위한 기능 묶음. 새 모델
  마이그레이션 1개(`migrations/0004_contest_presentation_minutes_and_more.py`), 프론트
  컴포넌트 3개(`CountdownTimer`/`PresentationSchedule`/`AwardCeremony`) 신설.
  - **잔여시간 카운트다운** — `대회 상세` 헤더에 진행중 상태일 때만 초 단위로 갱신되는
    종료까지 남은 시간을 표시(`CountdownTimer.tsx`). 5분 이하로 남으면 강조색으로 깜빡이고,
    지나면 "대회 종료 시각이 지났습니다"로 바뀐다. 서버 상태 변경 없이 순수 클라이언트 시계
    기반(`contest.end_at`)이라 서버 부하 없음.
  - **발표 일정 배정** — `Contest.presentation_start_at`(발표 시작 시각)·
    `presentation_minutes`(팀당 배정 시간, 기본 10분) 필드와 `Team.presentation_order`
    필드 추가. 운영자가 `POST /api/contests/<slug>/assign_presentation_order/`
    (`ContestViewSet.assign_presentation_order`, 운영자 전용)를 호출하면 제출 시각 순으로
    (미제출 팀은 이름순으로 맨 뒤에) 순번을 매기고, 시작 시각부터 팀당 배정 시간만큼
    순서대로 슬롯을 계산한다. 각 팀의 시작/종료 시각은 저장하지 않고
    `TeamSerializer`에서 매번 계산해 돌려주므로, `presentation_minutes`를 나중에 바꿔도
    재배정 없이 즉시 반영된다. 프론트(`PresentationSchedule.tsx`)는 팀·시각을 목록으로
    보여주고 현재 시각이 슬롯 안에 들어온 팀에 "발표 중" 배지를 붙인다.
  - **종합 순위(발표 점수 포함) 비공개, 심사위원 전용 열람** — 기존 `final`("결선") 라운드를
    "발표 점수를 포함한 종합 점수"로 의미를 확장하고, 시상 전까지 일반 참가자·관람객에게는
    보이지 않게 막았다. `ContestViewSet.scoreboard`가 요청자가 운영자(`is_staff`)이거나 이
    대회의 배정된 심사위원인지 확인해, 아니면 응답에서 `final` 라운드 엔트리 자체를 제외한다
    (점수를 마스킹하는 게 아니라 아예 안 보낸다). `preliminary`("예선", 코드/기능 점수)는
    지금처럼 항상 실시간 공개. 프론트는 심사위원·운영자가 아니면 결선 탭 자체를 숨기고
    "발표 점수를 포함한 종합 순위는 비공개이며, 시상식에서 공개됩니다" 안내를 보여준다
    (`ContestDetail.tsx`의 `visibleRounds`/`canSeeFinal`). 이걸로 "심사위원만 보는 탭"을
    별도 화면 대신 같은 스코어보드의 권한별 가시성으로 구현했다 — 새 API도, 새 화면도 없이
    기존 라운드 개념을 재활용.
  - **시상식 진행 도구** — 새 `Award` 모델(`contest`, `rank`, `title` — 예: 1위 "대상",
    2위 "우수상") + `AwardViewSet`(`/api/awards/`). `rank`·`title` 자체도 발표 전 유출을
    막기 위해 읽기 포함 전 구간을 운영자 전용으로 잠갔다(새 `IsOrganizer` 권한 —
    `IsOrganizerOrReadOnly`와 달리 `SAFE_METHODS` 예외 없음). 운영자 화면
    (`AwardCeremony.tsx`)에서 등수별 상 이름을 등록해 두고 "시상식 시작"을 누르면 전체 화면
    오버레이가 뜬다. 등수가 낮은 상(예: 우수상)부터 호명해 대상(1위)으로 마무리하도록
    순서를 뒤집어 진행하며, 클릭할 때마다 "호명하기 → 수상팀 공개 → 다음 시상"으로 한 단계씩
    넘어간다. 수상팀은 `final` 라운드 스코어보드의 `rank`와 `Award.rank`를 매칭해서
    가져온다 — 동점으로 그 등수에 해당하는 팀이 없으면 "순위 정보 없음"으로 안전하게
    표시한다(크래시 대신).
  - 백엔드 회귀 테스트 21건 추가(발표 순서 배정 4건, 종합 순위 비공개 4건, 상 관리 5건,
    기존 스코어보드 테스트 중 익명으로 결선을 보던 2건은 심사위원 인증으로 수정), 총 81건
    통과(SQLite, `DATABASE_URL=sqlite:///... manage.py test`). 프론트 `tsc -b && vite build`
    통과. 로컬에 시드 데이터(대회 2개, 팀 3개, 채점 완료)를 넣고 Chrome 확장으로 실제
    화면에서 로그인 → 카운트다운(진행중/만료 둘 다) → 발표 순서 배정·"발표 중" 배지 →
    익명/심사위원 스코어보드 가시성 차이 → 시상식 2단계(우수상→대상) 전체 흐름을 직접
    클릭해 확인했다. 확인 중 발표 순서를 배정해도 화면이 바로 갱신되지 않는 버그를 발견해
    수정(`PresentationSchedule`의 `onAssigned`가 대회 정보만 갱신하고 팀 목록은 다시 받아오지
    않던 문제 — `ContestDetail`에서 `refreshLive()`도 같이 호출하도록 수정).

- **디자인 기준 확정 + 화면 반영** (2026-09-06)
  `DESIGN.md`를 "이 문서가 단일 기준"으로 다시 쓰고(라이트/다크 토큰 표, 타입 스케일,
  상단 바·컨트롤 규격, 아이콘·모션 규칙, 화면별 기준), 프론트에 반영했다.
  - **타입 스케일** — `:root { font-size: 19px }` 한 값이 전체 레버다. 개별 크기는 모두
    `rem`이라 이 줄만 바꾸면 화면 전체가 같은 비율로 움직인다. 본문 약 17px, 화면 제목 약 30px.
  - **상단 바** — 높이 68px, 로고 마크 + 워드마크 + 대회명 브레드크럼, 오른쪽에 테마 토글과
    계정(`App.tsx`). 목록 화면에는 "대회" 제목 블록을 뒀다.
  - **로그인** — 비밀번호 확인란과 8자 미만·불일치 즉시 안내, 조건 미충족 시 제출 잠금
    (`AuthPanel.tsx`). 서버 검증은 그대로 두고 프론트는 안내만 한다.
  - **시상식** — 수상팀 공개 순간 컨페티 34조각 + 링 확산 + 수상 정보 상승 연출
    (`AwardCeremony.tsx`, `style.css`). 조각은 인덱스 기반 의사 난수라 리렌더에도 안 튀고,
    `prefers-reduced-motion`이면 연출을 걷어낸다. 무대는 테마와 무관하게 어두운 배경 고정
    (기존엔 `var(--ink)`를 써서 다크 모드에서 무대가 하얗게 뒤집혔다).
  - **심사 화면** — 팀별 접이식 행으로 바꿔 한 번에 한 팀만 펼친다. 접힌 상태에서도
    예선/결선 점수 요약이 보인다(`ContestDetail.tsx`의 `JudgePanel`).
  - 딩벳 문자(`▼`, `▲`, `×`, `🎉`)를 전부 인라인 SVG로 교체. 누르는 요소는 최소 44px.
  - 로컬 확인: Chrome으로 목록 → 상세 → 심사 접이식 → 시상식 컨페티(라이트·다크) 클릭 확인,
    `npm run build` 통과. 확인 중 로컬 `dev.sqlite3`에 마이그레이션 `0003`·`0004`가 밀려 있어
    적용했다.

- **학과/동아리 공모전 시범 적용** — 소규모 실사용 파일럿 진행, 피드백 반영해 반복 개선.
  운영자 계정은 `python manage.py createsuperuser`(또는 admin에서 `is_staff` 체크)로 만든다.
- (선택) 커스텀 도메인 연결 — 비용·절차는 위 [커스텀 도메인 (예정)](#커스텀-도메인-예정-2026-09-03-조사) 참고.
  프론트만 먼저 붙이는 구성 추천.
- (선택) 대회 기간 Render Starter 플랜으로 슬립 방지 (월 $7)
- (후속 과제) 스코어보드 WebSocket 전환 — 현재 5초 REST 폴링으로 파일럿 규모(수십 팀·수 명의
  심사위원)는 충분. Render 단일 프로세스에서 Django Channels + Redis를 붙이는 비용 대비 이점이
  작아 보류. 참가 팀이 수백 단위로 늘거나 폴링 부하가 문제 되면 재검토.

---

## 트러블슈팅 / 이슈 기록

과거에 겪은 문제와 해결 과정을 기록용으로 모아둔 섹션입니다.

### Render 배포: `relation "posts_post" does not exist` (500 에러)

원인: `Procfile`에 Heroku 방식인 `release: python manage.py migrate`를 썼는데, Render는
이 `release` 단계를 지원하지 않아 마이그레이션이 한 번도 실행되지 않음.
해결: `Procfile`을 `web: python manage.py migrate --noinput && gunicorn config.wsgi`
한 줄로 합쳐서, 매 배포/재시작 시 마이그레이션이 먼저 실행되도록 수정.

### Render 배포: Procfile을 고쳤는데도 그대로 500

원인: Render 대시보드의 **Start Command** 필드에 `gunicorn config.wsgi`가 직접
입력되어 있어 `Procfile` 내용을 완전히 무시하고 있었음 (대시보드 Start Command가
Procfile보다 우선 적용됨).
해결: 대시보드 Start Command 값을 위 한 줄로 직접 교체.

### Render 프로덕션에서 `/admin/`이 500 (2026-09-03, 해결됨)

증상: API는 정상인데 https://web-claude-t.onrender.com/admin/login/ 만 500.
원인: `settings.py`가 whitenoise의 `CompressedManifestStaticFilesStorage`를 쓰는데 배포
과정에 `collectstatic`이 없어 `staticfiles/` 매니페스트가 존재하지 않았음. `DEBUG=False`에서
admin 템플릿의 `{​% static %}`가 매니페스트를 찾다 `ValueError: Missing staticfiles manifest
entry`로 터짐(API 응답은 static을 쓰지 않아 멀쩡했음). 로컬에서 `DJANGO_DEBUG=False`로
재현 → `collectstatic` 후 200 확인.
해결: `Procfile`과 Render **Start Command**에 `python manage.py collectstatic --noinput`을
`migrate` 다음에 추가.

### Render 디버깅 팁

`DJANGO_ALLOWED_HOSTS`가 배포 도메인과 정확히 일치해야 함. 원인 파악이 안 될 때는
`DJANGO_DEBUG=True`로 잠깐 바꿔 Django 에러 페이지의 traceback을 직접 확인한 뒤 다시
`False`로 되돌리는 방식이 가장 빠름 (Render 접근 로그만으로는 500 원인이 안 보임).

### Windows 호스트에서 `npm run dev`가 `'vite' is not recognized`로 실패

원인: `node_modules`를 devcontainer(Linux)에서 설치한 채로 Windows npm으로 실행하면
`.bin`의 심볼릭 링크가 Windows용 실행 파일이 아님.
해결: Windows에서 직접 작업할 때는 `node_modules`를 지우고 Windows npm으로 다시
`npm install`.

### Vercel 프로덕션에서 모든 API 호출 404 (2026-09-02, 해결됨)

증상: 회원가입/로그인 등 백엔드 호출이 전부 "요청에 실패했습니다 (404)".
원인: Vercel의 `VITE_API_BASE_URL` 환경변수가 `https://web-claude-t.onrender.com`으로
설정되어 있었음(끝에 `/api` 누락). `frontend/src/api.ts`가 `${API_BASE_URL}${path}`
형태로 요청을 만들어 실제로는 `/auth/register/`로 나갔는데, Django에는 `/api/auth/register/`만
존재해 404. 배포된 JS 번들(`assets/index-*.js`)에서 baked-in 값을 직접 확인해 원인을
특정함.
해결: `VITE_API_BASE_URL`을 `https://web-claude-t.onrender.com/api`로 수정 후 재배포.
프로덕션에서 회원가입→자동 로그인까지 재검증 완료.

