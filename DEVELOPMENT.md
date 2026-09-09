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
| LLM 호출(팀빌딩 자동 정리, 심사 보조 예정) | 외부 API 왕복이 수 초~수십 초, gunicorn 워커는 1개 | 모든 제공사 호출에 30초 타임아웃, 실패해도 기능만 꺼지고 화면은 계속 동작 | 응답 없는 제공사 하나가 서비스 전체를 묶는 사고를 실제로 겪었다(NVIDIA, 아래 트러블슈팅) |

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

`backend/contests/tests.py`에 API 테스트 126건(인증·토큰 갱신, 대회 CRUD·상태 전환·삭제, 상태별
동작 제한, 팀/제출물 권한, 심사위원 배정, 스코어보드 순위 집계·비공개·캐시/ETag, 발표 순서,
시상, 팀빌딩 프로필·추천, GitHub 프록시, 쿼리 수 고정 검증)이 있습니다.
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
정한 것을 사실로 굳히지 않는다). 개인 GitHub 주소도 함께 받아 후보 목록에 링크로 노출한다 —
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

- **심사 보조: Claude API로 제출 저장소 사전 분석** (기획 확정, 미착수)

  **왜:** 심사위원은 팀당 10분 안에 저장소를 다 읽을 수 없어 사실상 README와 데모 링크만 보고
  점수를 매기게 된다. 심사 품질이 곧 대회 품질인데 여기가 가장 얇다. 저장소를 읽어오는 인프라
  (`backend/contests/github.py` 프록시: `repo`/`readme`/`tree`/`file`)는 이미 있으므로, 없는 것은
  "읽은 내용을 요약해 심사위원 앞에 놓는 단계"뿐이다.

  **무엇을 만드나:** 제출물 하나당 구조화된 사전 분석 1건.
  - 이 프로젝트가 실제로 하는 일 (README 주장이 아니라 코드 기준)
  - 구현된 것 vs 껍데기만 있는 것 (라우트·함수는 있는데 본문이 비어 있는 부분 등)
  - 기술 스택과 눈에 띄는 설계 판단
  - 심사 항목별 근거 (**점수는 제안하지 않는다** — 아래 참고)
  - 근거로 삼은 파일 경로 목록 (심사위원이 직접 열어 확인할 수 있게)

  **점수를 대신 매기지 않는 이유:** 제안 점수를 띄우면 심사위원이 거기에 닻을 내려(anchoring)
  결국 모델이 채점하는 것과 같아진다. 대회의 정당성이 걸린 부분이라, 출력은 "무엇이 있는지"까지만
  하고 판단은 사람이 한다. 심사위원이 근거를 직접 확인할 수 있도록 파일 경로를 반드시 붙인다.

  ### 착수 전 확정된 설계 판단

  **(1) 언제 부르나 — 제출 시점에 미리 계산한다. 심사 중에 부르지 않는다.**
  gunicorn 워커가 1개(`backend/Procfile`에 `-w` 없음)라, 동기로 Claude를 부르면 30초~수 분 동안
  **서비스 전체가 멈춘다** — 5초마다 도는 스코어보드 폴링까지 전부 타임아웃 난다. 그래서
  `repo_url` 이 등록·변경될 때 백그라운드로 분석해 DB에 저장해 두고, 심사 화면은 저장된 결과만
  읽는다. 대회 당일에는 이미 다 끝나 있어야 한다.
  - 배경 실행 수단이 지금 없다(Celery·RQ 없음). 가장 가벼운 선택지는 운영자가 심사 전에 한 번
    누르는 **일괄 분석 엔드포인트**를 두고, 그 안에서 **Batch API**(비동기가 기본이고 비용 50%)로
    전 팀을 한 번에 보내는 것. 폴링해서 완료되면 저장한다. 이러면 워커 블로킹도, 새 인프라도 없다.

  **(2) 프롬프트 캐싱을 반드시 건다.** 같은 저장소를 심사위원 여러 명이 열고, 재분석도 일어난다.
  저장소 내용을 캐시 접두사에 두고 질문만 뒤에 붙인다(`GITHUB_CACHE_SECONDS`를 둔 것과 같은 논리).

  **(3) 저장소를 통째로 넣지 않는다.** `github.py` 의 `tree` 로 파일 목록을 받은 뒤
  소스 파일만 골라(락파일·`node_modules`·바이너리·이미지 제외) 상한을 두고 넣는다. 잘라야 할 만큼
  크면 그 사실을 분석 결과에 명시한다(조용히 자르지 않는다).

  **(4) 저장소가 비공개거나 없을 수 있다.** GitHub 프록시가 404/403을 돌려주면 분석 결과에
  "저장소를 읽을 수 없었음"을 남기고, 심사 화면은 그대로 수동 심사로 진행한다. 분석 실패가 심사를
  막아서는 안 된다.

  **(5) 참가자에게는 보이지 않는다.** 결선 점수와 같은 등급으로 다룬다 — 운영자·배정된 심사위원만
  조회 가능(`IsOrganizer` / `Judge` 확인). 참가자가 자기 프로젝트 평가를 미리 보면 안 된다.

  **(6) 제공사 3사(Anthropic·OpenAI·Google)를 모두 지원하고, 분석할 때 모델을 고른다.** 확정.
  한 곳에 묶이지 않는 것 자체도 이유지만, 더 중요한 이유는 **품질을 비교할 수단이 생긴다**는 점이다.
  이 작업이 요구하는 건 요약이 아니라 "낯선 코드 50k 토큰을 읽고 주장과 실제를 대조하는" 일이라,
  저가 모델이 무너지는 지점이고 실패 방식도 고약하다(README 를 그럴듯하게 요약해 돌려준다 — 심사위원이
  이미 스스로 할 수 있는 일이라 기능이 있으나 마나가 된다). 어느 모델이 쓸 만한지는 **실제 제출물로
  재봐야** 알 수 있고, 모델 선택 기능이 그 실험을 일회성 스크립트가 아니라 서비스 기능으로 만든다.

  - **비교가 성립하려면 저장소 수집과 프롬프트가 완전히 같아야 한다.** 제공사별로 다른 것은
    호출부 하나뿐이도록 설계한다. 같은 입력·같은 프롬프트가 아니면 모델 비교가 아니라 프롬프트
    비교가 된다.
  - **비용은 선택 기준이 아니다.** 대회 1회 = 입력 약 1M 토큰인데, 저가 모델은 약 $0.1, 상위
    모델도 약 $6 수준이다. 1년에 두세 번 여는 서비스에서 가격 차이는 무의미하다 — 품질로 고른다.
  - **키가 있는 제공사만 목록에 뜬다.** `GITHUB_TOKEN` 과 같은 방식으로, 설정된 키가 하나도 없으면
    기능 전체가 비활성이다. 한 제공사가 죽어도 나머지는 계속 동작해야 한다.
  - **OpenAI 의 데이터 공유(무료 토큰) 프로그램은 쓰지 않는다.** 트래픽이 학습에 사용되는데 여기
    흐르는 건 학생들이 제출한 자기 코드다. 참가자 동의를 받지 않았고 받을 계획도 없다. 대회 규정
    문제이지 기술 선택 문제가 아니다.

  ### 구현 스케치

  - 모델: `SubmissionReview`(`submission` **ForeignKey**, `provider`, `model`, `summary`,
    `findings` JSON, `cited_paths` JSON, `input_tokens`/`output_tokens`, `status`(pending/done/failed),
    `error`, `created_at`; `unique_together = (submission, provider, model)`).
    **OneToOne 이 아니다** — 같은 제출물을 여러 모델로 돌려 나란히 보는 것이 이 기능의 목적 중
    하나이기 때문이다. 제출물이 바뀌면 그 제출물의 모든 분석을 무효화(재분석 대상)로 표시한다.
  - 제공사 어댑터: `backend/contests/llm/` 아래 얇은 인터페이스 하나
    (`analyze(repo_context, prompt) -> AnalysisResult`)와 제공사별 구현 3개. **프레임워크를 만들지
    않는다** — 저장소 수집·프롬프트 구성·결과 파싱은 공용이고, 제공사별 코드는 "요청을 보내고 텍스트와
    토큰 수를 돌려주는" 부분에 한정한다.
  - 백엔드: `backend/contests/judge_assist.py` — 저장소 수집 → 프롬프트 구성 → 어댑터 호출 → 결과 저장.
    엔드포인트 `POST /api/contests/<slug>/analyze_submissions/`(운영자 전용, 일괄 실행,
    `provider`/`model` 지정), `POST /api/submissions/<id>/analyze/`(운영자 전용, 한 팀만 다른 모델로
    재분석 — 비교용), `GET /api/submissions/<id>/reviews/`(운영자·심사위원 전용, 여러 건 반환).
  - 프론트: `SubmissionReview.tsx`(이미 GitHub 트리를 보여주는 심사 도구)에 분석 패널 추가 —
    분석이 여러 건이면 모델별로 탭/토글해 비교. 대회 상세에 운영자용 "심사 보조 분석 실행" 버튼과
    모델 선택기, 진행 상태 표시.
  - 설정: `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GOOGLE_API_KEY` 모두 선택. 설정된 것만 선택지에
    노출. 기본 모델은 설정으로 지정. 의존성은 쓰는 제공사의 SDK 만 추가.

  **심사위원은 모델을 고르지 않는다.** 분석은 심사 전에 미리 계산해 두므로(위 (1)), 선택 시점은
  운영자가 분석을 실행할 때다. 심사위원은 저장된 결과를 볼 뿐이다.

  **비용 감각:** 20팀 × 저장소 약 50k 입력 토큰 = 입력 1M 토큰. 저가 모델은 대회 1회 약 $0.1,
  상위 모델(Claude Opus 5 등)은 약 $6 + 출력분. Batch 를 쓰면 절반, 캐시가 걸리면 재분석은 더 싸다.
  어느 쪽이든 1년에 두세 번이면 무시할 수준이라, 가격이 아니라 품질로 고른다.

  **착수 전 남은 확인:** 어느 제공사 키를 실제로 확보할지, 심사 항목(예선=코드/기능, 결선=발표)에
  맞춰 분석 항목을 어떻게 나눌지, 완료를 무엇으로 확인할지(운영자가 화면에서 새로고침 vs 다음
  폴링에 실어 보내기), 3사 각각의 Batch/비동기 방식이 제각각이므로 1차에는 Batch 없이 백그라운드
  순차 실행으로 갈지.

  **첫 단계는 모델 비교다.** 실제 제출 저장소 3~5개를 골라 저가 모델과 상위 모델로 각각 돌리고,
  심사위원이 어느 쪽이 실제로 쓸 만한지 판단한다. 그 결과로 기본 모델을 정한다. 실험 비용은 몇 센트다.
  
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

- **기술 스택 입력: 자유 타이핑 → 정규 목록에서 다중 선택** (기획 확정 2026-09-09, 미착수)

  **왜:** 지금 `Profile.skills` 는 참가자가 쉼표로 직접 치는 자유 문자열이다(`ProfilePanel.tsx`
  "기술 스택 (쉼표로 구분)"). 오타와 표기 흔들림이 그대로 태그가 되어, `react` / `리액트` /
  `React.js` / `reactjs` 가 전부 다른 스택으로 세어진다. `matching.py` 의 `_normalize()` 는
  대소문자와 앞뒤 공백만 없애므로 이 차이를 못 잡는다. 결과로 **매칭이 조용히 망가진다** —
  `WEIGHT_SKILL_SPREAD`(스택이 덜 겹치는지)가 실제로는 "표기가 다른지"를 재게 되고, 팀 카드에
  같은 기술이 두세 번 다른 이름으로 뜬다. 틀렸다는 신호가 어디에도 안 나타나는 게 특히 나쁘다.

  **무엇을 만드나:** 정규 스택 목록을 두고 참가자는 그중에서 다중 선택한다. 목록의 출발점은
  Devpost 가 제출물에 붙이는 "Built With" 태그 목록을 참고한다(해커톤 제출물에서 실제로 쓰이는
  스택이 이미 추려져 있다). 화면 디자인은 확정 — 프로필 패널의 텍스트 입력 한 칸이 다중 선택
  컨트롤로 바뀌고, 나머지 프로필 레이아웃은 그대로다.

  **확정된 설계 판단 (2026-09-09):**

  **(1) 역할과 스택은 별도로 관리하고, 둘 다 다중 선택이다.** `KNOWN_ROLES` 방식을 스택으로
  넓히지 않는다 — 역할은 7개 고정 분류고 스택은 롱테일이라 관리 주기도 목록 크기도 다르다.
  다만 **입력 방식은 둘 다 같다: 목록에서 여러 개를 고른다.** 풀스택 개발자는 프론트엔드와
  백엔드를 함께 고르면 되고, 스택도 마찬가지로 제한 없이 겹쳐 고른다. 한쪽만 고르게 강제하는
  단일 선택은 쓰지 않는다.

  **(2) 목록은 DB 테이블로 관리한다.** 코드 상수(`KNOWN_ROLES` 방식)도 검토했으나 채택하지
  않았다. 결정적인 이유는 **운영자가 대회 중에 목록을 고칠 수 있어야 한다**는 것이다. 상수라면
  목록에 없는 스택이 대회 당일에 나왔을 때 재배포 전에는 손을 쓸 수 없고, Render 무료
  인스턴스라 재배포에는 콜드 스타트(30~50초)까지 따라붙는다. 대회 당일에 감수할 종류의 작업이
  아니다. DB 로 두면 목록 밖 태그를 그 자리에서 정규 목록으로 승격시킬 수 있고, 목록이 대회를
  거치며 자연스럽게 자란다.

  대신 치르는 비용을 분명히 해 둔다: 모델 + 마이그레이션 + CRUD API + 관리 화면 + 시드 데이터가
  한꺼번에 따라온다. 참고로 매칭에서 스택이 갖는 비중은 100점 중 10점
  (`matching.py` 의 `WEIGHT_SKILL_SPREAD`)으로, 역할의 45점(`WEIGHT_ROLE_GAP`)보다 한참 가볍다 —
  **이 기능은 매칭 정확도가 아니라 운영자의 손이 묶이지 않게 하려고 만드는 것**이다.

  **(3) 목록 관리 화면은 운영자(staff) 전용이고, 실제로 쓰기가 가능하다.** 참가자에게는 고르는
  UI만 보인다. 권한은 기존 `IsOrganizer` 를 그대로 쓴다. 화면이 하는 일:
  - 정규 목록 조회·추가·이름 수정
  - **참가자 프로필에 실제로 올라왔지만 목록에 없는 태그를 빈도순으로** 보여주고, 버튼 한 번으로
    정규 목록에 승격. 무엇을 추가해야 하는지 운영자가 추측하지 않아도 된다.
  - 삭제는 하드 딜리트가 아니라 **비활성 플래그**다. 이미 프로필이 참조 중인 스택을 지우면 과거
    프로필의 태그가 말없이 사라진다. 비활성 스택은 새로 고를 수 없고 기존 참조는 남는다.

  **(4) 목록은 백엔드가 정본이고, 프론트에 API로 내려준다.** 파이썬과 TypeScript 양쪽에 목록을
  두면 반드시 어긋난다. 키가 설정된 제공사 목록을 `available_models()` → `LlmModelsView` 로
  내려주는 방식이 이미 있으므로(`backend/contests/llm/base.py`, `views.py`) 같은 모양으로 하나 더
  만든다. (3)의 운영자 화면도 같은 엔드포인트를 읽는다.

  목록은 프로필 화면이 열릴 때마다 읽히므로 캐시를 건다. 여기서는 `LocMemCache` 로 충분하다 —
  워커를 늘렸을 때 무효화가 한 워커에만 닿는 문제는 남지만, **낡은 스택 목록의 최악은 방금 추가한
  태그가 몇 분간 안 보이는 것**이라 권한 판정 캐시를 보류한 이유(아래 항목: 심사위원에서 뺀 사람이
  결선 점수를 계속 봄)와는 위험의 성격이 다르다.

  **(5) 목록 밖 값은 버리지 않는다.** `profile_extract.py` 가 자기소개에서 뽑은 스택 중 목록에
  매핑되는 것은 정규 태그로, 매핑되지 않는 것은 **별도 필드에 그대로** 남긴다. `roles` 처럼
  버리면 실제로 쓴 기술이 사라지고, (3)의 승격 화면도 보여줄 것이 없어진다. 참가자가 UI 에서
  직접 타이핑하는 경로는 두지 않는다 — 그게 이 작업의 목적이다. 목록 밖 태그는 자기소개 자동
  정리에서만 생긴다.

  **(6) 별칭(alias)을 스택 레코드에 함께 둔다.** `react` / `React.js` / `reactjs` 가 같은 것을
  가리킨다는 사실을 어딘가에는 적어야 (5)의 매핑이 동작한다. 표시 이름과 별칭 목록을 같은 행에
  두고, LLM 이 뽑은 문자열을 별칭까지 훑어 매칭한다.

  **(7) 시드.** 빈 목록으로 배포하면 스택 선택 자체가 불가능하다. Devpost "Built With" 태그를
  참고한 초기 목록을 데이터 마이그레이션으로 넣는다.

  **(8) 기존 데이터.** 이미 저장된 자유 문자열 `Profile.skills` 를 정규 목록에 맞추는 일회성
  정리가 필요하다. 매핑되지 않는 값은 (5)의 별도 필드로 옮긴다.

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

