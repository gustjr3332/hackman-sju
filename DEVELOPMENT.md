# HACKMAN 개발 문서

개발·배포·운영자를 위한 문서다. 서비스 소개와 사용 방법은 [README.md](README.md), 화면 디자인
기준은 [DESIGN.md](DESIGN.md)를 본다. 여기에는 도메인 모델, 저장소 구조, 기술 스택, 로컬 개발 환경,
테스트, 배포, 로드맵, 트러블슈팅 기록을 둔다.

조사·판정 문서는 `docs/research/` 에 따로 둔다 —
[유사 프로젝트 조사](docs/research/similar-projects.md)(경쟁 제품 비교),
[AI 활용성 검토](docs/research/ai-utility-review.md)(무엇에 AI 를 쓰고 무엇에 안 쓸지의 순위와 근거).

- 백엔드: https://web-claude-t.onrender.com/api/contests/
- 프론트엔드: https://hackman-sju.vercel.app/

Django REST Framework + React(Vite) 기반 해커톤/공모전 운영 플랫폼입니다. 대회 생성 →
팀 구성 → 제출물 등록 → 심사위원 채점 → 실시간 스코어보드로 이어지는 흐름을 지원합니다.
우아한형제들 해커톤 운영 사례(예선 15분·결선 10분 실시간 집계)를 참고 모델로 삼았습니다.
향후 Flutter로 동일 Django REST API를 재사용하는 웹+앱 하이브리드 확장을 계획하고 있습니다.

## 도메인 모델

`Contest` — `Team` — `Participant` / `Submission` — `Judge` — `Score`

- 대회 상태: 모집중 / 진행중 / 심사중 / 종료. 운영자가 대회 상세 화면에서 **순서에 관계없이
  자유롭게** 전환한다(되돌리기·건너뛰기 모두 허용, 데이터는 그대로 남는다)
- 역할: 운영자(staff) / 참가자 / 심사위원
- 채점: 팀의 제출물 1건에 대해 심사위원별로 예선/결선 라운드 점수·코멘트 입력.
  같은 심사위원이 같은 라운드에 다시 저장하면 기존 점수를 덮어쓴다(upsert).
- 스코어보드: 라운드별 평균 점수·심사 수·**순위**를 집계. 동점은 같은 순위를 공유하고 다음
  순위는 건너뛴다(1, 1, 3). 점수가 없는 팀은 순위 없이 맨 아래에 표시. `preliminary`(예선,
  코드/기능 점수)는 항상 공개, `final`(결선, 발표 점수 포함 종합 점수)은 운영자·배정된
  심사위원에게만 공개(시상 전까지 비공개).
- 발표 일정: 이벤트 기반. `Team.presentation_order`(운영자가 자유 재배치)와
  `Team.presentation_minutes`(1~30분, 비우면 `Contest.presentation_minutes`)를 정해 두고,
  운영자가 "발표 시작"을 누른 시각을 `Team.presentation_started_at` 에 **기록**한다(예정표를
  계산하지 않는다). 진행 중인 팀만 `presentation_due_at`(시작 + 발표 시간)이 채워져 타이머가
  돈다 — 아무도 시작하지 않은 교체·쉬는 시간에는 타이머가 멈춘다. 운영자만 조작 가능.
- 시상: `Award`(대회, 등수, 상 이름)를 운영자가 미리 등록해 두고 시상식에서 등수별 최종
  순위와 매칭해 순서대로 공개. `Award`는 읽기 포함 운영자 전용.

대회 상태에 따라 서버가 허용하는 동작 (프론트는 같은 규칙으로 폼을 숨기고, 강제는 서버가 함):

| 동작 | 모집중 | 진행중 | 심사중 | 종료 |
|---|:-:|:-:|:-:|:-:|
| 팀 생성 / 참가 | O | – | – | – |
| 제출물 등록 / 수정 | O | O | – | – |
| 심사위원 채점 | – | – | O | – |
| 스코어보드 조회 | O | O | O | O |

허용되지 않는 상태에서 요청하면 `403` + `"… (현재 상태: 심사중)"` 형태의 메시지를 돌려준다.
규칙 정의: `backend/contests/views.py`의 `*_STATUSES`, `frontend/src/rules.ts`.

## 아키텍처 드라이버

파일럿 규모(수십 팀·심사위원 수 명, Render 무료/저가 인스턴스 1개)를 전제로, 구조를 바꾸는
대신 각 제약의 상한만 올리는 쪽으로 대응했다. 무엇을 포기했는지까지 함께 적는다.

| 드라이버 | 제약 | 대응 | 트레이드오프 |
|---|---|---|---|
| Render Free 콜드 스타트 | 15분 미사용 시 슬립, 첫 요청에 30~50초 | "서버를 깨우는 중입니다" 안내로 이탈만 방지, 대회 당일만 Starter 플랜으로 전환 | 지연 자체는 유료 플랜 없이 못 없앤다. 외부 cron ping 은 무료 인스턴스 시간 상한·정책 문제로 채택하지 않음 |
| 실시간 스코어보드 | 참가자·심사위원 다수가 동시에 갱신을 확인 | WebSocket 대신 5초 REST 폴링 + 서버 캐시(3초, 권한별 키 분리) + ETag 조건부 요청(304) + 지터(±20%) | 초 단위 지연이 남는다. 팀 수가 수백 단위로 늘면 Channels+Redis 재검토 |
| GitHub API 호출 한도 | 비인증 60회/시간 — 심사위원 여럿이 다른 저장소를 동시에 열면 금방 소진 | 프론트 직접 호출을 백엔드 프록시로 이전, 토큰으로 5000회/시간 확보 + 30분 캐시 | 서버가 대신 요청하므로 오픈 프록시가 되지 않게 저장소 URL 만 받아 owner/repo 를 직접 조립한다. 토큰 없이 배포하면 오히려 "서버 IP 하나가 60회"로 좁아진다 |
| LLM 호출(팀빌딩 자동 정리) | 외부 API 왕복이 수 초~수십 초, gunicorn 워커는 1개 | 요청 경로에서 부르는 호출에 30초 타임아웃, 실패해도 기능만 꺼지고 화면은 계속 동작 | 응답 없는 제공사 하나가 서비스 전체를 묶는 사고를 실제로 겪었다(NVIDIA, 아래 트러블슈팅) |
| LLM 호출(심사 보조 저장소 분석) | 저장소 수만 토큰을 읽는 호출이라 30초로는 정상 응답도 못 받는데, 요청 안에서 기다리면 워커 1개가 통째로 묶인다 | **요청 경로 밖(백그라운드 스레드)에서 실행**하고 제한 시간을 180초로 따로 잡는다. 엔드포인트는 '분석 중' 행만 만들고 즉시 응답하며 화면이 폴링한다 | 프로세스가 재시작되면(배포·슬립) 진행 중 분석이 사라지고 행이 '분석 중'으로 남는다 — 다시 실행하면 그만이라 복구 절차를 두지 않았다. Celery·RQ 를 들이지 않은 대가다 |

### 검증 중 발견: 조건부 폴링이 실제 브라우저에서 조용히 중단 (해결됨)

증상: 스코어보드 폴링에 ETag 조건부 요청을 붙인 뒤, 서버 로그에 `OPTIONS ... 200` 만 반복되고
`GET` 이 따라오지 않았다. 프리플라이트는 통과하는데 브라우저가 본 요청 자체를 보내지 않은 것.

원인: `django-cors-headers` 의 기본 허용 헤더 목록에 `If-None-Match` 가 없다. 응답 쪽을 여는
`CORS_EXPOSE_HEADERS` 와 요청 쪽을 여는 `CORS_ALLOW_HEADERS` 는 별개 설정이라, 전자만 넣으면
이 증상이 난다.

**curl 로는 재현되지 않는다** — curl 은 프리플라이트 규칙을 강제하지 않아 그냥 성공한다.
실제 브라우저로 끝까지 확인해야만 드러나는 종류의 버그였다.

해결: `CORS_ALLOW_HEADERS = (*default_headers, 'if-none-match')`. 회귀 테스트 3건을 추가하고,
고치기 전에 그 테스트가 실제로 실패하는지 먼저 확인했다. 프로덕션(Vercel 오리진)까지 재검증 완료.

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
| DB | PostgreSQL | 로컬 개발은 Docker(16), 운영은 Supabase 무료 플랜 |
| API 테스트 | Postman | `backend/postman/`에 컬렉션·환경 파일로 관리 |
| 배포(백엔드) | Render Web Service + Supabase PostgreSQL | gunicorn + whitenoise |
| 배포(프론트) | Vercel | Root Directory: `frontend` |
| 향후 하이브리드 앱 | Flutter | 같은 Django REST API 재사용 예정 |

## 로컬 개발 환경

### 백엔드

```bash
cd backend
docker compose up -d          # PostgreSQL 컨테이너 기동 (호스트 포트는 .env 의 POSTGRES_PORT)
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

`backend/contests/tests.py`에 API 테스트 155건(인증·토큰 갱신, 대회 CRUD·상태 전환·삭제, 상태별
동작 제한, 팀/제출물 권한, 심사위원 배정, 스코어보드 순위 집계·비공개·캐시/ETag, 발표 순서,
시상, 팀빌딩 프로필·추천, 정규 스택 목록, 심사 보조 분석, GitHub 프록시·첫 커밋 시각,
쿼리 수 고정 검증)이 있습니다.
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
- 운영자(staff) 계정은 목록 화면에서 **새 대회 만들기**, 상세 화면에서 **상태 전환**
  (네 상태 중 어디로든 자유롭게), 심사위원 배정, 발표 순서·시간 조정과 발표 시작/종료,
  맨 아래 **위험 구역**에서 대회 삭제를 할 수 있습니다.

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
- DB: Supabase PostgreSQL, `DATABASE_URL`로 연결 (아래 [데이터베이스](#데이터베이스-supabase) 참고)
- 환경변수: `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=False`,
  `DJANGO_ALLOWED_HOSTS=web-claude-t.onrender.com`, `DATABASE_URL`,
  `CORS_ALLOWED_ORIGINS=https://hackman-sju.vercel.app`
- **`GITHUB_TOKEN` — 심사 도구를 쓸 대회라면 사실상 필수.** 프록시로 옮기면서 GitHub 한도가
  "브라우저 IP 마다 60회/시간"에서 "서버 IP 하나로 60회/시간"으로 바뀌었다. 30분 캐시가
  대부분을 흡수하지만, 심사위원 여러 명이 서로 다른 저장소를 동시에 열면 토큰 없이는 오히려
  더 빨리 막힌다. 토큰을 넣으면 5000회/시간이 되어 문제가 사라진다. 공개 저장소만 읽으므로
  scope 없는 fine-grained 읽기 토큰으로 충분하다.
- 그 밖의 선택 환경변수: `SCOREBOARD_CACHE_SECONDS`(기본 3), `GITHUB_CACHE_SECONDS`(기본 1800)

### 데이터베이스 (Supabase)

2026-09-10 에 Render 관리형 PostgreSQL 에서 Supabase 무료 플랜으로 옮겼다. Render 무료 DB 는
생성 30일 뒤 삭제되어, 매달 백업하고 새로 만들지 않으면 데이터가 사라진다. 그 만료가 없는
무료 Postgres 중 대시보드에서 데이터를 바로 들여다볼 수 있는 곳으로 골랐다.

**Postgres 만 쓴다.** Supabase 의 Auth·Storage·Realtime 은 붙이지 않았다. 인증은 이미
SimpleJWT 가, 정적 파일은 whitenoise 가 맡고 있고 업로드 필드(`FileField`)는 한 개도 없다.
지금 이 프로젝트에 Supabase 는 "만료되지 않는 관리형 Postgres" 이상도 이하도 아니다.

```
[브라우저] ──▶ Vercel (정적 SPA)
           └─▶ Render Web Service ──▶ Supavisor 풀러 :5432 ──▶ Supabase PostgreSQL
                (Django + gunicorn)
[GitHub Actions] ─ 매일 keepalive 쿼리 / 매주 pg_dump
```

#### 연결 문자열은 반드시 "세션 풀러"

세 가지 문자열이 제공되는데 겉모습이 거의 같아서 헷갈린다. 구분은 스킴이 아니라 **호스트**다.

| | 유저명 | 호스트 | 포트 | 채택 |
|---|---|---|---|:-:|
| Direct | `postgres` | `db.<ref>.supabase.co` | 5432 | ✗ |
| Session pooler | `postgres.<ref>` | `aws-N-<region>.pooler.supabase.com` | 5432 | **○** |
| Transaction pooler | `postgres.<ref>` | `aws-N-<region>.pooler.supabase.com` | 6543 | ✗ |

- **Direct 를 쓰면 안 된다.** 무료 플랜의 direct 연결은 IPv6 전용이고 Render 아웃바운드는
  IPv4 다. 로컬(IPv6 있는 가정용 회선)에서는 붙어서 검증을 통과해 놓고 배포 후에만 연결
  타임아웃으로 죽는, 가장 나쁜 형태로 실패한다.
- **세션 모드(5432)** 는 일반 Postgres 와 동작이 같아 `migrate` 도 서버 사이드 커서도 그대로
  돈다. 트랜잭션 모드(6543)로 바꾸려면 `disable_server_side_cursors=True` 가 함께 필요하고,
  gunicorn 워커가 1개인 지금은 연결 수가 모자랄 일이 없어 이득이 없다.
- 유저명이 `postgres` 가 아니라 `postgres.<project-ref>` 인 것, 대시보드가 보여주는
  `[YOUR-PASSWORD]` 의 **대괄호는 자리표시자라 지워야 한다**는 것 두 가지가 자주 틀린다.
- 비밀번호에 `@ : / ? #` 가 있으면 URL 인코딩해야 한다. 귀찮으면 대시보드에서 영숫자
  비밀번호로 재설정하는 편이 빠르다.

#### Django 쪽 설정 (`backend/config/settings.py`)

```python
DATABASES = {'default': dj_database_url.parse(
    os.environ['DATABASE_URL'],
    conn_max_age=0,      # 풀러가 이미 연결을 재사용한다
    ssl_require=True,    # 풀러는 평문 연결을 거부한다
)}
```

`conn_max_age=0` 이 핵심이다. 풀러 뒤에서 Django 가 영속 연결을 붙들면 풀 슬롯만 차지하다
서버 쪽에서 끊기고, 다음 요청이 `InterfaceError` 로 실패한다.

#### 무료 플랜의 두 구멍과 대응

| 구멍 | 영향 | 대응 |
|---|---|---|
| 7일 무활동 시 프로젝트 일시정지 | 데이터는 남지만 서비스가 죽고 수동 복구가 필요 | `.github/workflows/supabase-keepalive.yml` — 매일 쿼리 1회 |
| 자동 백업 없음(유료만 일 1회) | 실수로 지우면 복구 불가 | `.github/workflows/supabase-backup.yml` — 주 1회 `pg_dump`, 아티팩트 90일 |

keepalive 는 Render 앱을 curl 하지 않고 **DB 를 직접 찌른다.** Render 무료 웹서비스는 유휴 시
잠들어 있어 curl 이 콜드스타트 타임아웃으로 실패할 수 있는데, 그러면 정작 막으려던 일시정지가
그대로 일어난다. 두 워크플로 모두 `SUPABASE_DB_URL` 리포지토리 시크릿(세션 풀러 URL 전체)을
쓴다.

백업 워크플로의 `PG_IMAGE` 는 **Supabase 서버의 메이저 버전과 맞춰야 한다.** 클라이언트가
서버보다 낮으면 `pg_dump` 가 버전 불일치로 거부하는데, 스케줄 실행이라 조용히 실패한다.
버전은 Supabase 대시보드 Settings → Infrastructure 에서 확인한다. 2026-09-10 기준 이 프로젝트는
`17.6.1.166`(메이저 17)이라 `postgres:17-alpine` 이 맞고, 수동 실행으로 백업 성공까지 확인했다.

#### 이전 절차 (재현 가능한 형태)

`pg_dump`/`pg_restore` 가 아니라 **Django 의 `dumpdata`/`loaddata` 로 옮겼다.** Render 가
PostgreSQL 18.6 이었고 Supabase 신규 프로젝트는 그보다 낮은 메이저 버전이라, 상위 버전 덤프를
하위 서버에 복원하는 지원되지 않는 조합이 된다. 스키마는 `migrate` 가 새로 만들고 데이터만
JSON 으로 옮기면 버전 문제가 통째로 사라진다. 데이터가 수십 건 규모라 가능한 선택이었고,
수만 건이었다면 버전을 맞춘 `pg_restore` 를 썼어야 한다.

```powershell
# 0. 안전 백업 (복원용이 아니라 보험. 로컬에 Postgres 클라이언트가 없어 Docker 로 실행)
docker run --rm -v ${PWD}:/backup postgres:18-alpine `
  pg_dump "<Render External URL>" -Fc -f /backup/backup.dump

# 1. 데이터만 추출. PYTHONUTF8 을 켜지 않으면 Windows 에서 cp949 로 저장된다(아래 트러블슈팅)
cd backend
$env:PYTHONUTF8 = "1"
$env:DATABASE_URL = '<Render External URL>'
.\.venv\Scripts\python.exe manage.py dumpdata `
  --natural-foreign --natural-primary `
  --exclude contenttypes --exclude auth.permission --exclude sessions `
  --indent 2 -o ..\data.json

# 2. Supabase 에 스키마 생성 후 주입
$env:DATABASE_URL = '<세션 풀러 URL>'
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py loaddata ..\data.json

# 3. 건수 대조 후 Render 대시보드에서 DATABASE_URL 교체 → Manual Deploy
```

- `contenttypes` 와 `auth.permission` 은 `migrate` 가 새로 만들므로 제외한다. 넣으면 PK 가
  충돌한다. `--natural-foreign` 이 있어야 `admin.logentry` 의 content_type FK 가 자연키로
  직렬화되어 새 DB 에서 해석된다.
- PowerShell 에서 환경변수를 넣을 때는 **작은따옴표**를 써야 한다. 큰따옴표 안에서는 비밀번호의
  `$` 가 변수로 해석되어 조용히 사라진다.
- `loaddata` 는 트랜잭션 안에서 돌기 때문에 실패해도 부분 적용되지 않는다. 고쳐서 다시 돌리면
  된다. 완료 시 시퀀스도 함께 리셋된다.
- 덤프 파일(`backup.dump`, `data.json`)은 해시된 비밀번호를 포함한 사용자 데이터라 `.gitignore`
  에 넣었다.
- **전환 후에도 Render DB 를 바로 지우지 않는다.** 1주일 두고 문제가 없으면 지운다(방치해도
  30일 뒤 자동 삭제된다).

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
5. DB는 손대지 않는다. 슬립은 웹 서비스 쪽 문제이고, DB 는 Render 밖(Supabase)에 있어
   인스턴스 타입 변경과 무관하다.

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
대회 CRUD. 상태(모집중·진행중·심사중·종료)는 운영자가 순서에 관계없이 자유롭게 오갈 수 있다
— 실수로 넘긴 단계를 되돌리거나 모집을 다시 열 수 있고, 되돌려도 팀·제출물·점수·시상은 그대로
남는다(상태 필드만 바뀐다). 상태에 따라 팀 생성/참가는 모집중에만, 제출물 수정은 모집중·진행중에,
채점은 심사중에만 허용되며 서버가 강제하고 프론트도 같은 규칙으로 폼을 잠근다.
운영자는 대회 상세 화면 맨 아래 "위험 구역"에서 대회를 삭제할 수 있다(테스트용 대회 정리용).
팀·참가자·제출물·심사위원·점수·시상이 함께 지워지고 되돌릴 수 없어, 대회 이름을 정확히 다시
입력해야만 삭제 버튼이 열린다. 삭제 후에는 목록 화면으로 돌아간다.

**팀·제출물**
팀 생성(자동 참가)·기존 팀 참가, 제출물(제목·설명·데모 링크·GitHub 저장소 링크) 등록·수정.
쓰기는 그 팀 참가자 또는 운영자만 가능하다.

**팀빌딩**
참가자는 계정에 프로필 하나를 둔다 — 자유 서술 원문과 거기서 뽑은 태그(기술 스택·관심 분야·
역할·숙련도)를 **둘 다** 보관하고, 자동 정리가 뽑은 결과도 참가자가 직접 고칠 수 있다(모델이
정한 것을 사실로 굳히지 않는다).
기술 스택은 자유 타이핑이 아니라 **정규 목록(`TechStack`)에서 다중 선택**한다 — 검색으로
좁히고 스크롤 영역에서 여러 개를 고른다. 목록의 출발점은 GitHub 이 실제로 쓰는 표기(Linguist
언어 + Topics)이고 130여 건을 시드로 넣어 두었으며, 정본은 DB 라 운영자가 대회 중에도 Django
admin 에서 고칠 수 있다(재배포 없이). 별칭을 같은 행에 둬서 `react` / `React.js` / `리액트` 가
한 태그로 접힌다. 목록에 매핑되지 않은 값은 **버리지 않고** `Profile.other_skills` 에 원문
그대로 남는다 — 버리면 참가자가 실제로 쓴 기술이 사라지고 운영자가 목록에 무엇을 추가해야
하는지도 알 수 없게 된다. 개인 GitHub 주소도 함께 받아 후보 목록에 링크로 노출한다 —
태그만으로는 안 보이는 실제 결과물을 팀이 직접 확인하는 통로다. 역할은 프론트엔드·백엔드·
모바일·디자인·데이터·기획·AI 일곱 가지이고, 서버가 목록 밖의 값을 걸러낸다.
자동 정리는 Anthropic·OpenAI·Google 중 **키가 설정된 제공사만** 선택지에 뜨고, 키가
하나도 없으면 그 버튼만 사라진다. 제공사별 코드는 "요청을 보내고 텍스트·토큰 수를 돌려주는"
호출부 하나로 한정하고 프롬프트 구성·파싱은 공용이라, 모델을 바꿔도 프롬프트 비교가 아니라
모델 비교가 된다(`backend/contests/llm/`). SDK 는 지연 임포트라 셋 다 설치하지 않아도 되고,
호출에는 30초 타임아웃이 걸린다 — 워커가 1개라 응답 없는 제공사 하나가 서비스 전체를 묶는다.
현재 실제로 검증된 것은 Google(`gemini-3.5-flash`)뿐이다. Claude·GPT 는 유료 키를 넣을 때
활성화된다.
모집중 화면에서 "나에게 맞는 팀"을 점수·근거와 함께 보여준다. **순위는 전부 규칙 기반이라
LLM 키 없이도 동작한다** — 팀에 없는 역할을 채우는지(45), 관심사가 겹치는지(25), 자리가
남았는지(20), 스택이 너무 겹치지 않는지(10). 자동 배정은 하지 않는다: 제안까지만 하고 참가는
사람이 직접 누른다. 이미 팀이 있거나 팀이 다 찼으면 추천이 뜨지 않고, 팀원·운영자는 반대로
"이 팀에 맞는 사람" 후보를 볼 수 있다(팀을 찾는 중으로 표시한 사람만).

**심사**
예선/결선 라운드별 점수(0~100, 0.5단위)+코멘트 입력(재입력 시 갱신, 동시 저장에도 안전).
심사 화면에서 제출물 데모 링크를 iframe으로 바로 시연하고, GitHub 저장소는 서버 프록시로
README·파일 트리·파일 내용을 열람할 수 있다.
**저장소 사전 분석**: 운영자가 심사 전에 실행하면 제출 저장소를 LLM 이 읽어 "실제로 하는 일 /
구현된 것 / 껍데기만 있는 것 / 참고할 사실"을 **근거 파일 경로와 함께** 정리해 둔다. **점수는
제안하지 않는다** — 제안 점수를 띄우면 심사위원이 거기 닻을 내려 결국 모델이 채점하는 것과
같아지기 때문이고, 그래서 모델에 점수 필드 자체를 두지 않았다(회귀 테스트로 고정). 같은
제출물을 여러 모델로 돌려 탭으로 나란히 비교할 수 있고, 분석 이후 제출물이 수정되면 낡은
분석임을 화면에 표시한다. 저장소가 비공개거나 LLM 이 실패해도 그 사실만 기록되고 심사는 그대로
수동으로 진행된다. 결과는 운영자·배정된 심사위원만 볼 수 있다(참가자는 403).
**첫 커밋 시각**도 함께 보여준다 — 교내 대회에서 실제로 확인하고 싶은 것은 코드 표절이 아니라
"대회 시작 전에 이미 만들어 둔 프로젝트를 냈는가"이고, 그건 저장소의 첫 커밋 하나로 드러난다.
`per_page=1` 과 `Link` 헤더의 마지막 페이지 번호를 쓰므로 커밋이 수천 개여도 왕복 두 번이면
끝난다. **판정은 하지 않는다** — 포크·저장소 이관·squash 로 시각이 앞설 수 있어, 화면은
"대회 시작 이전입니다 (직접 확인해 보세요)"까지만 말하고 판단은 심사위원이 한다.

**스코어보드**
라운드별 평균 점수·심사 수·순위(동점 공동순위, 다음 순위 스킵)를 실시간 집계. 예선은 항상
공개, 결선(발표 점수 포함 종합 점수)은 시상 전까지 운영자·배정된 심사위원에게만 공개된다.
5초 주기 폴링에 서버 캐시·조건부 요청(304)·지터를 적용해 갱신 부하를 최소화했다.

**대회 당일 운영**
진행중 상태의 종료까지 남은 시간 카운트다운, 등수별 상 이름을 미리 등록해 시상식에서 순서대로
수상팀을 공개하는 진행 도구(시상 정보는 공개 전까지 운영자 전용).
발표는 이벤트 기반이다 — 제출 시각순으로 순서를 한 번 배정한 뒤 운영자가 ↑↓로 자유롭게
재배치하고, 팀마다 발표 시간을 1~30분에서 따로 정할 수 있다(비우면 대회 기본값). 타이머는
운영자가 그 팀의 "발표 시작"을 눌러야 돌기 시작하고 "발표 종료"에서 멈추므로, 팀 교체·쉬는
시간에는 아무 타이머도 흐르지 않고 앞 팀이 늦어져도 뒤 팀 시간이 깎이지 않는다. 한 대회에서
두 팀이 동시에 발표할 수 없어, 새 팀을 시작하면 아직 안 끝난 팀은 자동으로 종료된다.

**디자인·UI**
헤어라인 리스트 + 히어로 스코어보드 중심의 디자인 시스템(`DESIGN.md`)과 라이트/다크 모드
(시스템 설정 자동 인식), 반응형 레이아웃, 인라인 SVG 아이콘, 최소 44px 터치 타겟.

**배포·인프라**
Render(백엔드)+Vercel(프론트) 무료 티어 배포. 콜드 스타트 시 "서버 깨우는 중" 안내, GitHub
API 호출은 서버 프록시(토큰 사용 시 시간당 5000회)로 캐싱. 백엔드 API 테스트 155건(SQLite로
로컬 실행 가능).

### 구현하며 바뀐 판단 (2026-09-11)

기획 단계에서 적어 둔 것과 실제 구현이 갈린 지점만 남긴다. 나머지 설계 판단은 그대로 지켰다
(점수를 제안하지 않는 것, 참가자에게 분석을 보이지 않는 것, 제공사 3사 지원, 목록 밖 스택을
버리지 않는 것).

- **Batch API 대신 백그라운드 스레드.** 기획은 "운영자가 누르는 일괄 분석 + Batch API(비동기
  기본, 비용 50%)"였다. 실제로는 3사의 Batch 방식이 제각각이라 1차에 넣으면 비교 가능한
  파이프라인이 흔들린다. 대신 `threading` 으로 요청 경로 밖에서 순차 실행한다 — Celery·RQ 없이
  워커를 막지 않는 가장 가벼운 방법이고, 진행 상태가 DB 행으로 그대로 보인다. 비용 절반은
  포기했지만 대회 1회 몇 달러 수준이라 판단 기준이 되지 못한다.
  치르는 대가는 문서 앞의 드라이버 표에 적었다(재시작 시 진행 중 분석 유실).
- **LLM 호출 제한 시간을 호출부가 정한다.** `llm/base.py` 의 30초 상수는 요청 경로 전용이었는데,
  저장소 수만 토큰을 읽는 호출은 30초로는 정상 응답도 못 받는다. `complete(..., timeout=)` 을
  열어 심사 보조만 180초를 쓴다. 프로필 추출은 30초 그대로다.
- **정규 스택 관리 화면은 React 가 아니라 Django admin.** 기획은 전용 운영자 화면(목록 CRUD +
  미등록 태그 빈도순 승격)이었다. 목록을 손보는 빈도(대회당 몇 건)에 비해 과했다. admin 은 이미
  있고, "무엇을 추가해야 하는지"는 프로필의 `other_skills` 에 원문이 남아 확인된다. 승격 버튼
  하나가 아쉬워지면 그때 만든다.
- **전 구간을 `gemini-3.5-flash` 로 만들고 확인했다.** 검증된 키가 Google 하나뿐이었기
  때문이다. 어댑터가 제공사 무관이라 코드 변경 없이 모델만 갈아끼우면 되고,
  `requirements.txt` 에 `anthropic==1.5.0` 을 넣어 뒀으므로 **Anthropic 키를 쓰려면 환경변수
  하나만 설정하면 된다**. 어느 모델이 실제로 쓸 만한지는 실제 제출물로 재봐야 알 수 있다
  (검토 문서 4순위 실험).

### 보류 중 (필요해지면 재검토)

- 커스텀 도메인 연결 — 절차는 [커스텀 도메인 (예정)](#커스텀-도메인-예정-2026-09-03-조사)
- 대회 기간 Render Starter 플랜 전환 — 절차는
  [대회 당일 Starter 플랜 전환](#대회-당일-starter-플랜-전환-슬립-방지-런북)
- 스코어보드 WebSocket 전환 — 참가 팀이 수백 단위로 늘거나 폴링 부하가 실제 문제가 될 때만
  검토(현재는 폴링+캐시로 충분)
- 학과/동아리 규모 실사용 파일럿 운영 중 — 피드백 계속 반영
- 참가자 피드백 다이제스트(심사 코멘트를 참가자에게 요약해 돌려주기) — **보류 (2026-09-11
  결정)**. [AI 활용성 검토](docs/research/ai-utility-review.md)가 2순위로 꼽은 항목이지만,
  지금은 심사 보조와 정규 스택 목록을 먼저 끝내기로 했다. 기술적으로 막힌 것은 없다 — 대회
  종료 후 실행이라 제약 충돌이 0이고, 필요한 것은 "참가자에게 원문까지 보여줄지 요약만
  보여줄지, 운영자 검토 게이트를 둘지"라는 운영 판단 하나다(검토 문서의 권고는 **요약만 +
  운영자 게이트**).
  **재검토 조건:** 파일럿 대회를 한 번 더 치른 뒤, 참가자 피드백 요구가 실제로 나올 때
- 심사 점수 outlier drop / z-score 정규화 — **착수 불가 판정 (2026-09-11)**. 최고·최저점 제외는
  심사위원 5명 이상에서만 뜻이 있는데 실제 평균이 3~4명이라, 둘을 빼면 표본이 한둘만 남아 평균이
  오히려 더 흔들린다. z-score 도 같은 표본 부족에 걸린다. 근거와 대안 검토는
  [AI 활용성 검토](docs/research/ai-utility-review.md) §6-1.
  **재검토 조건:** 한 대회의 심사위원이 5명 이상이 될 때. 그전까지 편차 문제에 남는 답은 사후
  보정이 아니라 채점 전 기준 맞추기(공통 루브릭·예시 채점)인데, 루브릭 모델이 없어 지금은 기능이
  아니라 운영 수칙이다(같은 문서 §6-6)

### 다음 작업

순서는 [AI 활용성 검토](docs/research/ai-utility-review.md)(2026-09-10)에서 정했고,
**① 심사 보조 저장소 분석**, **③ 정규 스택 목록**, 그리고 AI 를 쓰지 않는 **저장소 첫 커밋
시각 확인**까지 2026-09-11 에 구현해 「완료」로 옮겼다(구현하며 바뀐 판단은 아래 별도 절에
적었다). **② 참가자 피드백 다이제스트는 보류**로 결정했다 — 기술적 장애가 아니라 우선순위
판단이다(「보류 중」 참고).

그 검토가 꼽은 항목은 이로써 전부 처리됐다. 아래 남은 둘은 **코드가 아니라 판단·조건이
막고 있는 것들**이라 순서를 따로 매기지 않는다.

**정정 (2026-09-11):** 그 검토는 심사 점수 outlier drop 을 "비용 대비 효과가 가장 큰 항목"으로
꼽으면서, 심사위원이 실제로 몇 명인지에 따라 적용 대상이 없을 수도 있다는 단서를 함께 달았다
(검토 §8). 실제 평균 심사위원 수가 **3~4명**으로 확인되어 적용 조건(5명 이상)에 못 미친다 —
착수 불가로 판정하고 「보류 중」으로 옮겼다. 심사위원 간 점수 편차라는 약점은 그대로 남으며,
지금 규모에서는 값싼 해법이 없다는 것이 이번 확인의 결과다.

- **세종 포털 로그인 연동** — 자체 회원가입/로그인(현재 방식) 대신 세종대 포털 계정으로
  로그인하는 시스템 구축. 참고 라이브러리:
  https://github.com/Chuseok22/sejong-portal-login

  **조사 결과 (2026-09-07): 이 라이브러리는 Java/Spring Boot 이고 사설 Maven(Nexus)
  저장소로 배포된다.** OkHttp + jsoup 으로 포털에 로그인해 학과·학번·이름·학년·재학상태·
  이수학기를 긁어오고, `POST /api/sejong-portal`(id/pw) 컨트롤러도 함께 들어 있다.
  우리 백엔드는 Django(Python)이라 그대로 가져다 쓸 수 없다. **착수 전에 셋 중 하나를
  골라야 한다:**
  1. **Spring Boot 사이드카로 띄우고 Django가 HTTP로 호출** — 라이브러리를 그대로 쓰지만
     서비스가 하나 더 늘고, Render 무료 플랜에서 JVM 인스턴스가 따로 잠들어 콜드 스타트가
     두 배가 된다(로그인이 가장 느린 경로가 된다).
  2. **같은 스크래핑을 Python 으로 이식** — `requests` + `BeautifulSoup` 이면 OkHttp+jsoup
     과 같은 일이다. 서비스가 하나로 유지되고 콜드 스타트도 그대로지만, 포털 HTML 이 바뀌면
     우리가 고쳐야 한다.
  3. **연동 자체를 보류** — 지금의 자체 회원가입을 유지.

  **결정이 필요한 별개 문제: 우리 서버가 학생의 포털 비밀번호를 평문으로 받게 된다.**
  스크래핑 방식은 본질적으로 그렇다(SSO/OAuth 가 아니다). 저장하지 않고 확인 즉시 버리더라도
  전송 구간과 로그에 남지 않게 하는 설계가 필요하고, 세종대 포털 이용 정책상 허용되는
  방식인지도 확인해야 한다. 이건 코드 문제가 아니라 운영 판단이라 착수 전에 정해야 한다.

  **확정된 설계: 포털 인증은 최초 신원 확인 1회만, 이후 세션 유지는 지금의 JWT를 그대로 쓴다.**
  포털 로그인은 외부 사이트를 대신 로그인하는 방식이라 1회 호출이 수 초짜리 외부 왕복이다.
  매 로그인·매 토큰 갱신마다 포털을 부르면 (1) Render 콜드 스타트와 겹쳐 첫 진입이 1분을 넘고,
  (2) 포털 점검·장애가 곧 우리 서비스 로그인 전면 중단이 되며, (3) 반복 자동 로그인이 포털 쪽
  이상 트래픽으로 보일 수 있다. 그래서 포털은 "이 사람이 이 학번이 맞다"를 확인하는 데만 부르고,
  결과(학번·이름·학과)는 `User`에 저장한다. access 2시간 / refresh 14일(`SIMPLE_JWT`)이 이미
  세션 유지를 담당하므로 토큰 만료 때마다 포털을 다시 부를 이유가 없다. GitHub 프록시에
  `GITHUB_CACHE_SECONDS`를 둔 것과 같은 논리다(외부 API는 호출 자체를 줄이는 게 방어책).

- **권한 판정 캐시는 일단 보류 (재검토 조건 명시)** — 원래는 스코어보드 폴링이 매번 신원
  확인 쿼리 3개(simplejwt 의 `User` 로드, `Contest` slug 조회, `Judge ... exists()`)를
  던지는 것을 캐시로 없애려 했다. 실제로 보니 **셋 중 하나는 캐시가 아니라 중복 제거로
  해결됐다** — `ContestViewSet.get_queryset()` 이 이미 `is_judge` 를 EXISTS 로 annotate 해
  두는데 `scoreboard` 액션이 같은 질문을 한 번 더 쿼리하고 있었다(`is_contest_judge()` 로
  정리, 회귀 테스트 있음).

  남은 두 쿼리는 단일 인덱스 조회(PK/unique)뿐이라, 참가자 100명 기준 초당 40회 정도다.
  Postgres 에게 이 정도는 병목이 아니고, 반대로 권한 캐시는 낡으면 **무해하지 않다** —
  `LocMemCache` 가 프로세스 메모리(`backend/config/settings.py`)라 워커를 늘리는 순간
  무효화가 한 워커에만 닿고, 심사위원에서 뺀 사람이 다른 워커에서는 계속 결선 점수를 본다.
  스코어보드 캐시(최대 3초 낡음, 무해)와는 위험 성격이 다르다.

  **재검토 조건:** 동시 접속이 수백 명대로 올라가거나, gunicorn 워커/인스턴스를 늘리게 될 때.
  워커를 늘린다면 그때는 캐시를 추가하기 전에 Redis 로 옮기는 것이 먼저다(공유 캐시가 되어야
  무효화가 모든 워커에 닿는다).

---

## 트러블슈팅 / 이슈 기록

과거에 겪은 문제와 해결 과정을 기록용으로 모아둔 섹션입니다.

### NVIDIA build API: 추론 호출만 무응답 (2026-09-08, 미해결·기능 제거)

증상: `integrate.api.nvidia.com` 에 채팅 완성 요청을 보내면 **HTTP 응답 바이트가 하나도 오지
않고** 무기한 멈춘다. 브라우저의 build.nvidia.com 플레이그라운드에서는 같은 모델이 정상 동작.

원인 좁히기(전부 무응답으로 동일):

| 요청 | 결과 |
|---|---|
| `GET /v1/models` | 200, 0.13초 — 키 유효 |
| 없는 모델 ID | 404, 0.12초 — 라우팅 정상 |
| 깨진 JSON | 500, 0.12초 — 파서 정상 |
| 인증 헤더 없음 | 401, 0.13초 — 인증 정상 |
| **유효 모델 + 정상 요청** | **무응답** |
| 유효 모델 + 스키마 오류(`messages` 누락) | **무응답** |
| 서로 다른 API 키 3개 | 전부 무응답 |
| 모델 4종(kimi-k3, nemotron-3-ultra, deepseek-v4-flash, llama-vision) | 전부 무응답 |
| `Accept: text/event-stream` + `stream:true`, 90초 대기 | **응답 헤더조차 없음** |
| DNS 가 주는 엣지 IP 2개 각각 | 전부 무응답 |
| 대조군: 같은 머신에서 Gemini 생성 | 200, 1.7초 |

배제된 것: 모델 ID(없는 모델은 즉시 404 이고, 유효 모델은 스키마 오류를 내도 똑같이 멈춘다),
API 키(3개 모두 `models` 조회는 200), `Accept` 헤더, 스트리밍(스트리밍은 생성 전에 헤더를 먼저
보낸다), 엣지 노드, 로컬 프록시(환경변수 없음, 같은 머신에서 Gemini 정상), TLS 재협상(정상
동작하는 `models` 조회도 같은 재협상을 한다).

남는 사실: **게이트웨이 계층(인증·검증·라우팅)은 0.12초에 응답하는데, 요청이 추론 백엔드로
넘어가는 순간에만 응답이 사라진다.** 브라우저에서는 되므로 계정 문제가 아니라 이 개발 환경에서
NVIDIA 추론 경로에 도달하지 못하는 것으로 보인다. 더 좁히지 못해 제공사 목록에서 제거했다.

이 일로 얻은 것: **모든 제공사 호출에 30초 타임아웃**(`contests/llm/base.py`
`REQUEST_TIMEOUT_SECONDS`). gunicorn 워커가 1개라 응답 없는 호출 하나가 서비스 전체를 묶는다 —
Claude·GPT 유료 키를 붙일 때도 같은 사고를 막는다.

### Docker Postgres 가 5432 에 바인딩되지 않음 (2026-09-11, 해결됨)

증상: `docker compose up -d` 는 성공하는데 Django 가 `connection refused` 로 붙지 못한다.
강제 재생성하면 진짜 원인이 나온다 —
`ports are not available: ... bind: An attempt was made to access a socket in a way forbidden
by its access permissions`. 포트를 쓰는 프로세스는 아무것도 없다(`netstat` 에 안 잡힌다).

원인: **Windows 가 5432 를 예약 범위로 잡고 있다.** Hyper-V/WSL 이 동적 포트 대역을 예약하는데
그 범위가 5432 를 삼킬 수 있다. 확인:

```powershell
netsh int ipv4 show excludedportrange protocol=tcp
# 예: 5430 ~ 5529 가 예약돼 있으면 5432 도 5433 도 못 쓴다
```

해결: 호스트 쪽 포트를 예약 범위 밖으로 옮긴다. `docker-compose.yml` 의 매핑을
`"${POSTGRES_PORT:-5432}:5432"` 로 두고 `.env` 의 `POSTGRES_PORT` 만 바꾸면 compose 와 Django 가
같은 값을 본다(예: `55432`). **컨테이너 안쪽은 5432 그대로**라 이미지도 덤프도 영향이 없다.
`winnat` 서비스를 재시작하는 방법도 있으나 관리자 권한이 필요하고 재부팅하면 다시 예약된다.

### 심사 보조 분석이 "JSON 을 찾지 못했습니다"로 실패 (2026-09-11, 해결됨)

증상: 첫 실전 실행(공개 저장소 `pallets/flask`)에서 GitHub 수집과 LLM 호출은 끝까지 갔는데
결과가 `failed` + `응답에서 JSON 을 찾지 못했습니다` 로 남았다.

원인: 출력 상한(`MAX_OUTPUT_TOKENS`)을 3000 으로 잡았는데, 수만 토큰짜리 입력을 읽는 모델은
**본문을 내기 전에 추론에 출력 예산을 먼저 쓴다.** 예산이 거기서 끝나면 응답 본문이 비어
돌아오고, 파서는 "JSON 이 없다"고만 말한다 — 진짜 원인(상한 부족)을 가리키는 단서가 없었다.

해결 두 가지:
1. `MAX_OUTPUT_TOKENS` 를 8000 으로 올렸다.
2. **빈 응답을 원인과 함께 실패시킨다.** `llm/base.py` 의 Google 어댑터가 `text` 가 비면
   `finish_reason` 을 담아 `LlmError` 를 낸다. 엉뚱한 메시지로 한 번 헤맨 대가다.

실측(참고): `pallets/itsdangerous` 기준 **27초, 파일 23개, 입력 17.5k 토큰, 출력 764 토큰**.
큰 저장소일수록 GitHub 파일 조회(파일당 1회)가 시간을 지배하므로 `GITHUB_TOKEN` 을 넣어 두는
편이 낫다.

### SQLite 로 테스트가 안 돌았다: `Connection() got an unexpected keyword argument 'sslmode'` (2026-09-11, 해결됨)

증상: 위 「백엔드 테스트」에 적힌 그대로
`$env:DATABASE_URL = "sqlite:///$PWD/test.sqlite3"; python manage.py test` 를 돌리면
`TypeError: Connection() got an unexpected keyword argument 'sslmode'` 로 죽는다. Postgres 가
없는 환경에서 테스트를 돌리는 경로 전체가 막혀 있었다.

원인: Supabase 이전 때 `DATABASE_URL` 분기에 `ssl_require=True` 를 넣었는데
(`dj_database_url.parse`), 이 옵션은 URL 종류와 무관하게 `OPTIONS['sslmode']` 를 채운다.
sqlite3 드라이버는 그런 인자를 모른다. Supabase 연결에는 아무 문제가 없어 그동안 드러나지 않았다.

해결: `ssl_require` 를 Postgres URL 에만 건다(`not url.startswith('sqlite')`). 운영 경로의
동작은 그대로다 — Supabase URL 은 여전히 SSL 을 요구한다.

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

### Supabase 이전: `dumpdata` 결과가 cp949 로 저장됨 (2026-09-10, 해결됨)

증상: `loaddata` 가 `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xb5 in position 1549`
로 실패. 0xb5 는 cp949 의 한글 바이트다(`1등` 의 `등`).

원인: Django 의 `dumpdata -o` 는 출력 파일을 `open(path, "w")` 로 열어 **로케일 인코딩**을
쓴다. 한국어 Windows 에서는 cp949 다. `loaddata` 는 UTF-8 로만 읽는다. `>` 리다이렉션을
피하려고 `-o` 를 썼는데, `-o` 자체에 같은 함정이 있었다.

해결: `dumpdata` 실행 전에 `$env:PYTHONUTF8 = "1"`. 이미 만들어진 파일은 cp949 로 읽어
UTF-8 로 다시 쓰면 된다(cp949 로 쓰는 데 성공했다면 한글 손실은 없다).

### Supabase 이전: PowerShell 이 바이너리·문자열을 망가뜨리는 두 지점 (2026-09-10)

- `pg_dump ... > backup.dump` — PowerShell 5.1 은 파이프 출력에 UTF-8 인코딩을 걸어 바이너리
  덤프를 손상시킨다. 반드시 볼륨 마운트 + `-f` 로 파일을 직접 쓰게 한다.
- `$env:DATABASE_URL = "...$..."` — 큰따옴표 안에서 비밀번호의 `$` 가 변수로 해석되어 조용히
  사라진다. 연결 문자열은 **작은따옴표**로 넣는다.

둘 다 에러 없이 잘못된 결과만 남기므로, 증상이 한참 뒤에 엉뚱한 곳에서 나타난다.

### Windows 에 Postgres 클라이언트가 없을 때 (2026-09-10)

`winget install PostgreSQL.PostgreSQL.17` 이 EnterpriseDB 다운로드에서
`0x80190193 : Forbidden (403)` 로 실패했다. 설치를 포기하고 Docker 이미지에 들어 있는
클라이언트를 그대로 썼다:

```powershell
docker run --rm -v ${PWD}:/backup postgres:18-alpine pg_dump "<URL>" -Fc -f /backup/backup.dump
docker run --rm postgres:18-alpine psql "<URL>" -c "select version()"
```

서버 버전에 맞춰 태그만 바꾸면 되므로(`postgres:17-alpine` 등) 버전 여러 개를 다뤄야 하는
이전 작업에는 설치본보다 오히려 낫다. `psql ... -c "select version()"` 은 Django 를 끼우지
않고 연결 문자열만 검증할 수 있어 원인 분리에도 쓸 만하다.

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

