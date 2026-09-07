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
│   │                           permissions, views, github, migrations)
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

`backend/contests/tests.py`에 API 테스트 74건(인증·토큰 갱신, 대회 CRUD·상태 전이, 상태별
동작 제한, 팀/제출물 권한, 심사위원 배정, 스코어보드 순위 집계·비공개·캐시/ETag, 발표 순서,
시상, GitHub 프록시, 쿼리 수 고정 검증)이 있습니다.
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
- 대회 상세 화면은 **약 5초마다**(화면끼리 요청이 겹치지 않도록 ±20% 흔들림) 팀 목록·스코어보드·
  대회 상태를 다시 가져옵니다(탭이 백그라운드면 건너뛰고, 다시 보이면 즉시 갱신). 운영자가 상태를
  바꾸면 참가자·심사위원 화면도 다음 폴링에서 폼 잠금/해제가 따라갑니다. 종료된 대회는 30초
  주기로만 확인합니다. 상단 `LIVE · 5초마다 갱신 · hh:mm:ss` 표시로 마지막 갱신 시각을 확인할 수
  있고, 요청이 실패하면 `연결 끊김 · 재시도 중`으로 바뀝니다.
- 스코어보드 폴링은 조건부 GET 입니다. 직전 응답의 `ETag` 를 `If-None-Match` 로 보내고, 순위가
  그대로면 서버가 `304`(본문 없음)를 돌려줍니다. 프론트는 이전 배열을 **같은 객체 참조로**
  재사용하므로 재렌더도 일어나지 않습니다. 로그인/로그아웃 시에는 응답이 계정에 따라 달라지므로
  이 캐시를 비웁니다.
- 첫 대회 목록 요청이 2.5초 안에 오지 않으면 "서버를 깨우는 중입니다 N초" 안내가 뜹니다
  (Render Free 슬립 대비). 추가 요청을 보내지는 않고, 이미 나간 요청이 끝나면 사라집니다.
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
- **`GITHUB_TOKEN` — 심사 도구를 쓸 대회라면 사실상 필수.** 프록시로 옮기면서 GitHub 한도가
  "브라우저 IP 마다 60회/시간"에서 "서버 IP 하나로 60회/시간"으로 바뀌었다. 30분 캐시가
  대부분을 흡수하지만, 심사위원 여러 명이 서로 다른 저장소를 동시에 열면 토큰 없이는 오히려
  더 빨리 막힌다. 토큰을 넣으면 5000회/시간이 되어 문제가 사라진다. 공개 저장소만 읽으므로
  scope 없는 fine-grained 읽기 토큰으로 충분하다.
- 그 밖의 선택 환경변수: `SCOREBOARD_CACHE_SECONDS`(기본 3), `GITHUB_CACHE_SECONDS`(기본 1800)

### 대회 당일 Starter 플랜 전환 (슬립 방지 런북)

Render Free 는 15분 미사용 시 잠들고 첫 요청에 30~50초가 걸린다. 프론트에 "서버를 깨우는
중입니다" 안내를 넣어 이탈은 막았지만(체감 개선), 지연 자체는 유료 플랜으로만 없앨 수 있다.
외부 cron 으로 주기적 ping 을 넣어 깨워 두는 방법은 무료 인스턴스 시간 상한과 Render 정책상
회색지대라 **채택하지 않는다.**

대회 전날 ~ 다음날까지만 Starter($7/월, 일할 계산)로 올린다.

1. **전날**: Render 대시보드 → `web-claude-t` → Settings → Instance Type → **Starter**.
   저장하면 재배포되므로 하루 전에 해서 배포가 정상인지 확인한다.
2. 브라우저로 https://web-claude-t.onrender.com/api/contests/ 를 열어 즉시 응답하는지 확인.
   5분 뒤 다시 열어 여전히 즉시 응답하면 슬립이 꺼진 것이다.
3. **대회 당일**: 참가자 가입·팀 구성은 되도록 미리 끝내 둔다(Starter 여도 첫 배포 직후
   몇 초는 느릴 수 있다).
4. **다음날**: Instance Type 을 Free 로 되돌린다. 되돌리는 것을 잊으면 계속 과금된다 —
   대회 종료 직후 캘린더에 알림을 걸어 둔다.
5. DB(관리형 PostgreSQL)는 손대지 않는다. 슬립은 웹 서비스 쪽 문제다.

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

도메인별로 지금 동작하는 기능만 정리한다. 구현 경위·버그 수정 이력·리뷰 기록은 git 로그로
충분해 여기서는 뺐다.

**인증·권한**
JWT 로그인/회원가입, 액세스 토큰 만료 시 자동 재발급(실패하면 재로그인 안내). 역할은
운영자(staff)·참가자·심사위원 3종이며, 심사위원은 대회별로 운영자가 배정·해제한다(채점
이력이 있으면 해제 불가).

**대회 관리**
대회 CRUD, 상태는 모집중→진행중→심사중→종료 순서로만 전이(역행·건너뛰기 불가, 운영자만
변경 가능). 상태에 따라 팀 생성/참가·제출물 수정은 모집중·진행중에만, 채점은 심사중에만
허용되며 서버가 강제하고 프론트도 같은 규칙으로 폼을 잠근다.
운영자는 대회 상세 화면 맨 아래 "위험 구역"에서 대회를 삭제할 수 있다(테스트용 대회 정리용).
팀·참가자·제출물·심사위원·점수·시상이 함께 지워지고 되돌릴 수 없어, 대회 이름을 정확히 다시
입력해야만 삭제 버튼이 열린다. 삭제 후에는 목록 화면으로 돌아간다.

**팀·제출물**
팀 생성(자동 참가)·기존 팀 참가, 제출물(제목·설명·데모 링크·GitHub 저장소 링크) 등록·수정.
쓰기는 그 팀 참가자 또는 운영자만 가능하다.

**심사**
예선/결선 라운드별 점수(0~100, 0.5단위)+코멘트 입력(재입력 시 갱신, 동시 저장에도 안전).
심사 화면에서 제출물 데모 링크를 iframe으로 바로 시연하고, GitHub 저장소는 서버 프록시로
README·파일 트리·파일 내용을 열람할 수 있다.

**스코어보드**
라운드별 평균 점수·심사 수·순위(동점 공동순위, 다음 순위 스킵)를 실시간 집계. 예선은 항상
공개, 결선(발표 점수 포함 종합 점수)은 시상 전까지 운영자·배정된 심사위원에게만 공개된다.
5초 주기 폴링에 서버 캐시·조건부 요청(304)·지터를 적용해 갱신 부하를 최소화했다.

**대회 당일 운영**
진행중 상태의 종료까지 남은 시간 카운트다운, 제출 시각순 발표 순서 자동 배정(운영자 재배정
가능)과 팀별 발표 시작/종료 시각 자동 계산, 등수별 상 이름을 미리 등록해 시상식에서 순서대로
수상팀을 공개하는 진행 도구(시상 정보는 공개 전까지 운영자 전용).

**디자인·UI**
헤어라인 리스트 + 히어로 스코어보드 중심의 디자인 시스템(`DESIGN.md`)과 라이트/다크 모드
(시스템 설정 자동 인식), 반응형 레이아웃, 인라인 SVG 아이콘, 최소 44px 터치 타겟.

**배포·인프라**
Render(백엔드)+Vercel(프론트) 무료 티어 배포. 콜드 스타트 시 "서버 깨우는 중" 안내, GitHub
API 호출은 서버 프록시(토큰 사용 시 시간당 5000회)로 캐싱. 백엔드 API 테스트 77건(SQLite로
로컬 실행 가능).

### 보류 중 (필요해지면 재검토)

- 커스텀 도메인 연결 — 절차는 [커스텀 도메인 (예정)](#커스텀-도메인-예정-2026-09-03-조사)
- 대회 기간 Render Starter 플랜 전환 — 절차는
  [대회 당일 Starter 플랜 전환](#대회-당일-starter-플랜-전환-슬립-방지-런북)
- 스코어보드 WebSocket 전환 — 참가 팀이 수백 단위로 늘거나 폴링 부하가 실제 문제가 될 때만
  검토(현재는 폴링+캐시로 충분)
- 학과/동아리 규모 실사용 파일럿 운영 중 — 피드백 계속 반영

### 다음 작업

- **세종 포털 로그인 연동** — 자체 회원가입/로그인(현재 방식) 대신 세종대 포털 계정으로
  로그인하는 시스템 구축. 참고 라이브러리:
  https://github.com/Chuseok22/sejong-portal-login
  착수 전 확인할 것: 라이브러리가 다루는 언어/런타임이 Django(Python) 백엔드와 맞는지,
  포털 계정과 기존 `User` 모델(운영자/심사위원 role 포함)을 어떻게 매핑할지, 자동화가
  세종대 포털 이용 정책상 허용되는 방식인지.

  **확정된 설계: 포털 인증은 최초 신원 확인 1회만, 이후 세션 유지는 지금의 JWT를 그대로 쓴다.**
  포털 로그인은 외부 사이트를 대신 로그인하는 방식이라 1회 호출이 수 초짜리 외부 왕복이다.
  매 로그인·매 토큰 갱신마다 포털을 부르면 (1) Render 콜드 스타트와 겹쳐 첫 진입이 1분을 넘고,
  (2) 포털 점검·장애가 곧 우리 서비스 로그인 전면 중단이 되며, (3) 반복 자동 로그인이 포털 쪽
  이상 트래픽으로 보일 수 있다. 그래서 포털은 "이 사람이 이 학번이 맞다"를 확인하는 데만 부르고,
  결과(학번·이름·학과)는 `User`에 저장한다. access 2시간 / refresh 14일(`SIMPLE_JWT`)이 이미
  세션 유지를 담당하므로 토큰 만료 때마다 포털을 다시 부를 이유가 없다. GitHub 프록시에
  `GITHUB_CACHE_SECONDS`를 둔 것과 같은 논리다(외부 API는 호출 자체를 줄이는 게 방어책).

- **대회 상태를 운영자가 자유롭게 전환(모집중 ↔ 진행중)** — 현재
  `ContestSerializer.ALLOWED_NEXT_STATUS`(`backend/contests/serializers.py`)가 모집중→
  진행중→심사중→종료 순서로만, 인접 상태로만(역행·건너뛰기 금지) 전환을 허용한다. 요구사항은
  운영자가 모집중·진행중 사이를 양방향으로 자유롭게 오갈 수 있게 하는 것. 착수 전 정할 것:
  자유화 범위가 심사중·종료까지 포함되는지(예: 심사중 → 진행중으로 되돌리기도 허용할지),
  진행중 → 모집중으로 되돌릴 때 이미 생긴 팀·제출물을 그대로 둘지(삭제하지 않는 것이 기본이어야
  함), 프론트 `StatusControl`(`ContestDetail.tsx`, 현재 "다음 단계 버튼만 활성화")도 같이
  풀어야 한다는 점.

- **팀 모집은 모집중 상태에서만, 마감 안내 메시지는 완전 제거** — 두 가지 확정된 변경.
  (1) 팀 생성/참가를 지금의 `TEAM_FORMATION_STATUSES = {RECRUITING, ONGOING}`
  (`backend/contests/views.py`)에서 `{RECRUITING}`만으로 좁힌다 — 진행중(ongoing)에는 더 이상
  새 팀을 만들거나 참가할 수 없다. **제출물 수정 허용 상태(`SUBMISSION_STATUSES`)는 건드리지
  않는다** — 이번 요청은 "팀 모집"에 한정되고, 제출물은 지금처럼 모집중·진행중에 계속 수정
  가능해야 한다. 프론트 `rules.ts`의 `canFormTeams`도 같은 기준으로 맞춘다.
  (2) `ContestDetail.tsx`의 `팀 모집이 마감되었습니다 (현재 상태: …)` `lock-hint` 메시지
  (약 252번째 줄, 팀 만들기 폼 바로 아래)는 대체 문구 없이 완전히 제거한다.

- **팀별 발표 시간·순서를 운영자가 자유 설정 + 수동 시작 방식으로 전환** — 현재는
  `Contest.presentation_minutes` 하나로 전 팀이 동일한 발표 시간을 쓰고, 순서는 제출 시각순
  자동 배정(`assign_presentation_order`)뿐이며, 각 팀 시작/종료 시각은 저장 없이
  `presentation_start_at + presentation_minutes * (order - 1)` 공식으로 매번 계산해 돌려준다
  (`TeamSerializer._slot_bounds`). 새 요구사항:
  - 팀마다 발표 시간을 개별로 1분 단위, 1~30분 범위에서 자유롭게 설정
  - 발표 순서도 운영자가 수동으로 자유롭게 재배치 가능(현재는 자동 배정만 있음)
  - 팀 교체·쉬는 시간에는 타이머가 흐르지 않음
  - 다음 팀 준비가 되면 운영자가 "발표 시작" 버튼을 눌러야 그 팀의 타이머가 시작

  설계 영향이 크다: 지금의 "저장 없이 공식으로 계산"하는 방식은 시작 시각이 고정된
  스케줄(clock-based)일 때만 성립한다. 버튼을 누른 시점이 곧 시작 시각이 되는 이벤트 기반
  모델로 바뀌므로, 팀별 실제 시작 시각(과 개별 발표 시간)을 저장하는 구조로 옮겨야 한다.
  `PresentationSchedule.tsx`·`CountdownTimer.tsx`·`Team`/`Contest` 모델·
  `assign_presentation_order` 엔드포인트 전체를 다시 설계해야 하는 작업.

- **권한 판정 캐시 (운영자·심사위원)** — 스코어보드 집계는 캐시했는데 "너 누구고 심사위원이냐"를
  묻는 쿼리는 매 폴링마다 그대로 나간다. 본문을 하나도 내려보내지 않는 304 응답조차 DB를 3번 친다:
  simplejwt 의 `User` 로드, `get_object()` 의 `Contest` slug 조회,
  `Judge.objects.filter(...).exists()`(`backend/contests/views.py:119` — `is_privileged` 가
  캐시 키를 고르므로 캐시 조회보다 먼저 실행된다). 폴링 간격이 5초
  (`frontend/src/ContestDetail.tsx:33`)이므로 참가자 100명이면 초당 20요청 × 3쿼리 =
  **초당 약 60쿼리가 신원 확인에만 쓰인다.**

  권한 데이터는 조회가 압도적으로 많고 대회 중엔 거의 안 바뀌어서 캐시 효율이 좋다. 무효화는
  이미 있는 `invalidate_scoreboard()` 패턴을 그대로 복제하면 된다 — `JudgeViewSet` 등 쓰기
  경로에서 해당 유저·대회의 권한 키를 지운다.

  **주의: 스코어보드 캐시와 달리 낡은 값이 무해하지 않다.** 캐시가 `LocMemCache`
  (프로세스 메모리, `backend/config/settings.py:130`)이고 `Procfile` 에 `-w` 플래그도
  `WEB_CONCURRENCY` 도 없어 지금은 gunicorn 워커가 1개라 우연히 성립한다. 워커나 인스턴스를
  늘리는 순간 무효화가 한 워커에만 닿고, 심사위원에서 뺀 사람이 다른 워커에서는 계속 결선
  점수를 보게 된다(스코어보드는 최대 3초 낡을 뿐이라 무해했지만 이건 다르다). 그래서 쓰기 시
  무효화에만 기대지 말고 TTL 을 짧게(30~60초) 같이 걸어 최악의 낡음을 제한한다. Redis 도입은
  실제로 스케일아웃할 때로 미룬다(현재 의존성에 브로커 없음).

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

