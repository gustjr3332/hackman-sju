# HACKMAN 백엔드 개발 문서 (Supabase 네이티브)

**범위: 백엔드 + AI.** 프론트엔드 화면·디자인 규칙은 다루지 않는다 — 필요하면
[docs/REFERENCE.md](docs/REFERENCE.md)의 "프론트엔드 디자인 시스템"을 본다. 서비스 소개·
사용법은 [README.md](README.md). 왜 지금 구조로 왔는지의 결정 기록은
[docs/REFERENCE.md](docs/REFERENCE.md)의 "설계 결정 기록"에 있다.

이 문서는 러닝 자료를 겸한다 — 무엇을 했는지뿐 아니라 **왜 그렇게 했는지**, Postgres/Supabase의
어떤 기능이 어떤 문제를 풀어 주는지를 실제 코드 위치와 함께 적는다.

- 프로덕션: https://ugrooqkeyhgldrtdriba.supabase.co (Postgres + Edge Functions)
- 프론트엔드(범위 밖): https://hackman-sju.vercel.app/
- 옛 Django 백엔드(`backend/`) 코드는 2026-09-19에 저장소에서 제거했다. Render 서비스도
  2026-09-23에 삭제 완료. Django 테이블 백업/drop만 아직 남아 있다(10장).

## 목차

1. [시스템 한눈에](#1-시스템-한눈에)
2. [도메인 모델](#2-도메인-모델)
3. [Postgres: 스키마와 보안 모델](#3-postgres-스키마와-보안-모델)
4. [Edge Functions: AI·외부 API 계층](#4-edge-functions-ai외부-api-계층)
5. [AI 활용 설계](#5-ai-활용-설계)
6. [로컬 개발 환경](#6-로컬-개발-환경)
7. [테스트](#7-테스트)
8. [배포](#8-배포)
9. [트러블슈팅](#9-트러블슈팅)
10. [남은 일](#10-남은-일)

---

## 1. 시스템 한눈에

```
프론트(범위 밖) → supabase-js → ┬─ PostgREST (테이블 자동 REST API, RLS 가 권한을 거른다)
                                  ├─ RPC (Postgres 함수를 REST 로 호출 — 트랜잭션이 필요한 쓰기)
                                  ├─ Realtime (DB 변경을 브로드캐스트)
                                  ├─ Auth (auth.users, JWT 발급)
                                  └─ Edge Functions (Deno) → LLM 3사 · GitHub API
                                                            → Postgres (service_role 로 직접 접근)
```

**핵심 설계 원칙 하나:** 로직은 최대한 Postgres 안(RLS·트리거·RPC)에 둔다. Edge Function은
**DB가 할 수 없는 일**(외부 HTTP 호출 — LLM, GitHub API)에만 쓴다. 이렇게 나누는 이유:

- DB 안의 로직은 **어떤 클라이언트로 접근해도 같은 규칙이 적용된다**(REST 로 직접 찔러도,
  Studio 에서 봐도, 나중에 앱을 만들어도 우회할 수 없다). 서버 코드 계층에 로직을 두면
  그 계층을 거치지 않는 경로가 생길 때마다 구멍이 될 잠재력이 생긴다.
- Edge Function 은 상태가 없고(요청마다 새로 뜬다) 벽시계 150초 제한이 있어, "DB 트랜잭션
  하나로 끝나는 일"에는 오히려 안 맞는다.

이 원칙이 3장·4장의 경계선이다: **"쓰기 하나가 여러 검증을 거쳐야 한다"는 Postgres RPC**,
**"외부 API 를 불러야 한다"는 Edge Function**.

## 2. 도메인 모델

`Contest` — `Team` — `Participant` / `Submission` — `Judge` — `Score`

- 대회 상태: 모집중(`recruiting`) / 진행중(`ongoing`) / 심사중(`judging`) / 종료(`closed`).
  운영자가 **순서에 관계없이 자유롭게** 전환한다(되돌리기·건너뛰기 모두 허용, 데이터는 그대로 남음).
- 역할: 운영자(`profiles.is_staff`) / 참가자 / 심사위원(대회별로 배정).
- 채점: 팀의 제출물 1건에 대해 심사위원별로 예선(`preliminary`)/결선(`final`) 라운드 점수·
  코멘트. 같은 심사위원이 같은 라운드에 다시 저장하면 upsert.
- 스코어보드: 라운드별 평균 점수·심사 수·순위. 동점은 같은 순위를 공유하고 다음 순위는
  건너뛴다(1, 1, 3). 점수가 없는 팀은 순위 없이 맨 아래. 예선은 항상 공개, 결선은 운영자·
  배정된 심사위원에게만(시상 전까지 비공개).
- 발표 일정: 이벤트 기반. `presentation_order`(운영자가 재배치)와 `presentation_minutes`를
  미리 정해 두고, 운영자가 "발표 시작"을 누른 시각을 `presentation_started_at`에 **기록**한다
  (예정표를 미리 계산하지 않는다). 진행 중인 팀만 타이머가 돈다.
- 시상: `Award`(대회, 등수, 상 이름)를 운영자가 미리 등록해 두고 시상식에서 등수별 최종 순위와
  매칭해 순서대로 공개. 읽기 포함 운영자 전용.

대회 상태에 따라 서버가 허용하는 동작(강제는 전부 3장의 트리거가 한다):

| 동작 | 모집중 | 진행중 | 심사중 | 종료 |
|---|:-:|:-:|:-:|:-:|
| 팀 생성 / 참가 | O | – | – | – |
| 제출물 등록 / 수정 | O | O | – | – |
| 심사위원 채점 | – | – | O | – |
| 스코어보드 조회 | O | O | O | O |

## 3. Postgres: 스키마와 보안 모델

전부 `supabase/migrations/20260915000000_schema.sql` 한 파일에 있다(613줄). 순서대로 읽으면
"테이블 → 헬퍼 함수 → 트리거 → RLS 정책 → 뷰 → RPC"로 진행된다 — 아래 절도 이 순서를 따른다.

### 3.1 테이블 (11개)

`profiles, contests, teams, participants, submissions, judges, scores, awards, tech_stacks,
submission_reviews, github_cache`. 사용자 테이블은 따로 없다 — Supabase Auth의 `auth.users`
(이메일·비밀번호 해시 등 인증 전용 데이터)와 `public.profiles`(이 앱이 아는 사용자 데이터:
아이디·스태프 여부·팀빌딩 프로필)를 `id`(uuid)로 1:1 연결한다. 이 분리가 Auth 커스터마이징을
이해하는 열쇠다(3.4절).

### 3.2 RLS: 기본은 잠금, 정책으로 연다

Supabase는 `public` 스키마의 테이블을 `anon`(비로그인)·`authenticated`(로그인) 역할에
REST로 자동 노출한다. **RLS(Row Level Security)를 켜지 않으면 아무나 전체를 읽고 쓸 수 있다**
— 실제로 이 프로젝트에서 Django 시절 테이블에 이 사고가 났었다(`docs/REFERENCE.md` 2.1절).

RLS를 켠 테이블은 기본이 "전부 거부"이고, `create policy`로 구멍을 낸 만큼만 열린다. 예:

```sql
-- supabase/migrations/20260915000000_schema.sql:356-359
create policy "누구나 조회" on public.contests for select to anon, authenticated using (true);
create policy "운영자만 생성" on public.contests for insert with check (public.is_organizer());
create policy "운영자만 수정" on public.contests for update using (public.is_organizer());
create policy "운영자만 삭제" on public.contests for delete using (public.is_organizer());
```

`using`은 "이 행을 보여줄지"(select/update/delete 대상 필터), `with check`는 "이 값으로
쓰는 걸 허용할지"(insert/update 결과 검증) — 이 둘이 다른 이유는 update에서 갈린다: 자기
팀 이름은 바꿀 수 있어도(using 통과), 다른 팀 소유로 넘기는 건 막아야(with check) 할 수 있다.

### 3.3 SECURITY DEFINER 헬퍼 — RLS 정책 안의 재귀를 피한다

```sql
-- supabase/migrations/20260915000000_schema.sql:157-170
create function public.is_organizer() returns boolean
language sql stable security definer set search_path = '' as $$
  select coalesce((select is_staff from public.profiles where id = auth.uid()), false)
$$;

create function public.is_judge_of(p_slug text) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (select 1 from public.judges where contest_slug = p_slug and user_id = auth.uid())
$$;
```

`is_organizer()`가 `profiles`를 조회하려면 `profiles`의 RLS를 통과해야 하는데, 그 RLS 정책이
다시 `is_organizer()`를 부르면 무한 재귀다. `security definer`는 이 함수를 **함수 소유자
권한으로** 실행해 호출자의 RLS를 우회한다 — 그래서 함수 안에서 직접 판단 로직을 짜고, 호출
쪽(정책)에서는 결과만 쓴다.

**`set search_path = ''`를 반드시 붙이는 이유:** `security definer` 함수는 소유자 권한으로
돌기 때문에, 스키마 이름을 안 쓰고 `profiles`라고만 쓰면 호출자가 자기 세션에 같은 이름의
가짜 테이블을 만들어 그걸 대신 읽게 만들 수 있다(스키마 하이재킹). 그래서 이 프로젝트의 모든
`security definer` 함수는 `search_path`를 비우고 모든 테이블명을 `public.`으로 완전하게 쓴다.
이건 일반적인 Postgres 보안 관행이지 이 프로젝트만의 습관이 아니다.

### 3.4 BEFORE 트리거 — RLS로 못 하는 "상태 게이팅"

RLS는 "이 행에 접근 가능한가"만 답할 수 있고 **왜 안 되는지 메시지를 못 준다**. "모집중일
때만 팀 생성 가능" 같은 규칙은 트리거로 처리한다:

```sql
-- supabase/migrations/20260915000000_schema.sql:187-204
create function public.ensure_status(p_slug text, p_allowed text[], p_message text) returns void
language plpgsql stable security definer set search_path = '' as $$
declare v_status text;
begin
  select status into v_status from public.contests where slug = p_slug;
  if v_status is not null and not (v_status = any (p_allowed)) then
    raise exception '% (현재 상태: %)', p_message, public.status_label(v_status) using errcode = '42501';
  end if;
end $$;

create function public.gate_team_insert() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  perform public.ensure_status(new.contest_slug, array['recruiting'], '모집중 상태에서만 팀을 만들 수 있습니다.');
  return new;
end $$;
create trigger teams_gate before insert on public.teams
  for each row execute function public.gate_team_insert();
```

같은 패턴이 `gate_participant_insert`(팀 참가), `gate_submission`(제출물 등록/수정/삭제),
`gate_score`(채점)에 반복된다 — 공통 검증(`ensure_status`)을 한 곳에 두고, 트리거마다
"어떤 상태에서 허용되는지" 배열과 메시지만 다르게 넘긴다.

**다른 종류의 트리거 — 자동 참가:**

```sql
-- :207-216
create function public.join_creator() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  if auth.uid() is not null then
    insert into public.participants (team_id, user_id) values (new.id, auth.uid());
  end if;
  return new;
end $$;
create trigger teams_join_creator after insert on public.teams
  for each row execute function public.join_creator();
```

팀을 만든 사람을 그 팀에 자동으로 넣는다 — 클라이언트가 "팀 생성"과 "참가"를 두 번
호출할 필요가 없다. `AFTER INSERT`(만든 뒤 실행)와 `BEFORE INSERT`(게이팅, 막을지 먼저 결정)의
차이를 보여주는 예다.

**되돌릴 수 없는 조작을 막는 트리거:**

```sql
-- :255-266
create function public.guard_judge_delete() returns trigger
language plpgsql security definer set search_path = '' as $$
declare v_count int;
begin
  select count(*) into v_count from public.scores where judge_id = old.id;
  if v_count > 0 then
    raise exception '이미 채점한 심사위원은 해제할 수 없습니다 (입력한 점수 %건).', v_count using errcode = '42501';
  end if;
  return old;
end $$;
create trigger judges_guard before delete on public.judges
  for each row execute function public.guard_judge_delete();
```

심사위원을 지우면 `scores`가 FK CASCADE로 같이 지워져 순위가 조용히 바뀐다. 채점 이력이
있으면 삭제 자체를 막아 "몰래 순위가 바뀌는" 사고를 원천 차단한다.

### 3.5 Auth 커스터마이징: 가입하면 프로필이 자동으로 생긴다

```sql
-- :271-279
create function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  insert into public.profiles (id, username)
  values (new.id, coalesce(new.raw_user_meta_data ->> 'username', split_part(new.email, '@', 1)));
  return new;
end $$;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();
```

Supabase Auth가 관리하는 `auth.users`에 직접 `after insert` 트리거를 걸 수 있다는 게 요점이다
— 회원가입 API를 따로 만들 필요 없이, `supabase.auth.signUp()`이 끝나면 이 트리거가
`profiles` 행을 만든다.

**로그인은 아이디 또는 이메일 — 그런데 GoTrue(Supabase Auth)는 이메일만 받는다.** 아이디로
로그인하려면 "아이디 → 이메일"을 어딘가에서 조회해야 하는데, 이걸 클라이언트가 부를 수 있는
RPC로 만들면 그 자체가 "아이디만 대면 이메일을 알아내는" 구멍이 된다. 그래서 별도의
Edge Function(`supabase/functions/login/index.ts`)을 하나 판다 — **유일하게 로그인 없이
호출 가능한 함수**다:

```ts
// supabase/functions/login/index.ts (핵심만)
let email = identifier.trim();
if (!email.includes('@')) {
  const { data: profile } = await admin.from('profiles').select('id').eq('username', email).single();
  const found = profile ? (await admin.auth.admin.getUserById(profile.id)).data.user?.email : null;
  email = found ?? `${crypto.randomUUID()}@invalid.local`;   // 없어도 같은 실패 경로를 타게
}
const res = await fetch(`${SUPABASE_URL}/auth/v1/token?grant_type=password`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', apikey: ANON_KEY },
  body: JSON.stringify({ email, password }),
});
```

조회는 `service_role`(RLS를 완전히 우회하는 관리자 키)로 서버 안에서만 하고, 결과 이메일은
클라이언트로 **절대 돌려주지 않는다** — GoTrue의 토큰 발급 결과(성공/실패)만 그대로 전달한다.
존재하지 않는 아이디도 가짜 이메일로 같은 실패 경로를 태워, "이 아이디가 있는지 없는지"조차
응답으로 구분되지 않게 한다. **이게 이 프로젝트에서 가장 좋은 보안 설계 예시다** — 필요한
기능(아이디 로그인)과 지켜야 할 것(이메일 비공개, 계정 존재 여부 비공개)이 충돌할 때, 조회
자체를 없애는 대신 "조회는 하되 결과를 절대 내보내지 않는 경계"를 만들었다.

### 3.6 Realtime: 데이터가 아니라 신호를 보낸다

```sql
-- :305-324
create function public.broadcast_contest_change() returns trigger
language plpgsql security definer set search_path = '' as $$
declare
  j jsonb := to_jsonb(coalesce(new, old));   -- 테이블마다 열이 달라 레코드 직접 접근 대신 jsonb로
  v_slug text;
begin
  v_slug := case tg_table_name
    when 'contests' then j ->> 'slug'
    when 'scores' then (select t.contest_slug from public.submissions s join public.teams t on t.id = s.team_id
                          where s.id = (j ->> 'submission_id')::bigint)
    -- ... teams/participants/submissions 도 각자 방식으로 contest_slug 를 찾는다
  end;
  if v_slug is not null then
    perform realtime.send(jsonb_build_object('table', tg_table_name), 'changed', 'contest:' || v_slug, false);
  end if;
  return null;
end $$;
```

`contests`·`teams`·`participants`·`submissions`·`scores`가 바뀔 때마다 `contest:{slug}`
채널로 "바뀌었다"만 보낸다. **점수 자체는 페이로드에 절대 안 싣는다** — 이 채널은 공개
채널이라 누구나 구독할 수 있고, 결선 점수를 페이로드에 실으면 결선 비공개 규칙이 통째로
새 나간다. 화면은 신호를 받으면 `scoreboard()` RPC(3.7절)를 다시 불러서 권한에 맞게 걸러진
데이터를 받는다 — **권한 검사가 필요한 데이터는 브로드캐스트하지 않고, 신호로 재조회를
유도한다**는 것이 일반화할 수 있는 패턴이다.

### 3.7 RPC: 트랜잭션 하나로 묶어야 하는 쓰기

REST(PostgREST)는 테이블 하나에 대한 CRUD만 한다. "여러 검증을 거쳐 여러 테이블에 걸친
쓰기를 원자적으로" 해야 하면 함수로 빼서 RPC로 부른다.

**upsert + 요청자 자기 자신으로 고정 (클라이언트를 신뢰하지 않는다):**

```sql
-- :492-513
create function public.submit_score(p_submission_id bigint, p_round text, p_value numeric,
                                    p_comment text default '')
returns setof public.score_list
language plpgsql security definer set search_path = '' as $$
declare v_slug text; v_judge bigint; v_id bigint;
begin
  select t.contest_slug into v_slug
  from public.submissions s join public.teams t on t.id = s.team_id where s.id = p_submission_id;
  ...
  select id into v_judge from public.judges where contest_slug = v_slug and user_id = auth.uid();  -- 클라이언트가 judge_id 를 못 고른다
  if v_judge is null then
    raise exception '이 대회의 심사위원으로 등록되어 있지 않습니다.' using errcode = '42501';
  end if;
  insert into public.scores (submission_id, judge_id, round, value, comment)
  values (p_submission_id, v_judge, coalesce(p_round, 'preliminary'), p_value, coalesce(p_comment, ''))
  on conflict (submission_id, judge_id, round)
  do update set value = excluded.value, comment = excluded.comment
  returning id into v_id;
  return query select * from public.score_list where id = v_id;
end $$;
```

클라이언트는 `judge_id`를 보내지 않는다 — 서버가 `auth.uid()`로 "요청자가 이 대회의 어느
심사위원인지"를 직접 찾는다. 클라이언트가 보낸 식별자를 그대로 믿으면 다른 심사위원 행세를
할 수 있으므로, **"누구인지"는 항상 서버가 세션에서 다시 확인한다.**

**윈도우 함수로 순위 매기기:**

```sql
-- :465-488 (발췌)
create function public.scoreboard(p_slug text)
returns table (team_id bigint, team_name text, submission_title text, round text,
               average_score numeric, vote_count int, rank int)
language sql stable security definer set search_path = '' as $$
  with rounds as (
    select r, o from unnest(array['preliminary', 'final']) with ordinality as u (r, o)
    where r = 'preliminary' or public.is_organizer() or public.is_judge_of(p_slug)  -- 결선 필터가 여기
  ), agg as ( ... avg(sc.value) as avg_raw, count(sc.id)::int as vote_count ... )
  select team_id, team_name, submission_title, round, round(avg_raw, 2), vote_count,
    case when avg_raw is not null
      then (rank() over (partition by round, avg_raw is null order by avg_raw desc))::int end
  from agg
  order by o, avg_raw desc nulls last, team_name collate "C"
$$;
```

`rank() over (...)`가 "동점 공동 순위, 다음 순위 건너뛰기"(1, 1, 3)를 만든다 — SQL의
순위 윈도우 함수 3형제는 `rank()`(공동 순위 후 건너뜀), `dense_rank()`(공동 순위 후 안 건너뜀),
`row_number()`(무조건 순차)다. 이 스코어보드가 원하는 규칙이 정확히 `rank()`다. **결선 필터는
RPC 안에서 SQL로 걸러진다** — 권한 없는 사용자에게는 애초에 `final` 행 자체가 안 만들어진다
(나중에 지우는 게 아니라 처음부터 생성하지 않는다).

같은 파일에 발표 진행 RPC(`start_presentation`/`end_presentation`/`reset_presentation`,
"다른 진행 중인 팀을 자동으로 끝낸다"는 로직 포함)와 `assign_judge`(아이디→uuid 조회 후 배정,
중복은 `unique_violation`을 잡아 한국어 메시지로 변환)도 있다 — 패턴은 위 둘과 같다.

**내부 함수는 REST로 못 부르게 잠근다:**

```sql
-- :604-612
revoke execute on function public.ensure_status(text, text[], text) from anon, authenticated, public;
revoke execute on function public.gate_team_insert() from anon, authenticated, public;
...
```

트리거 안에서만 쓰는 함수(`ensure_status`, `gate_*`, `handle_new_user` 등)는 `revoke execute`로
막는다 — Postgres 함수는 기본적으로 `public` 역할에 실행 권한이 있고, RPC로 노출하고 싶지 않은
내부 헬퍼까지 REST API(`/rpc/함수명`)로 호출 가능한 상태로 남는다. 신뢰 경계를 나눌 때
"클라이언트가 직접 부를 함수"와 "트리거 안에서만 쓸 함수"를 구분하고 후자는 명시적으로 잠가야
한다는 걸 보여주는 부분이다.

## 4. Edge Functions: AI·외부 API 계층

`supabase/functions/`, Deno 런타임. 제약: 벽시계 150초(무료), CPU 2초, 메모리 256MB.

### 4.1 공통 코드 (`_shared/`)

- **`http.ts`** — `serveAuthed(handler)`: OPTIONS는 CORS로 끝내고, 나머지는 `caller(req)`로
  로그인 여부를 확인한 뒤에만 handler를 부른다. `caller()`는 `Authorization` 헤더의 JWT를
  `admin.auth.getUser(token)`으로 검증하고 `profiles`에서 아이디·스태프 여부를 붙여 돌려준다.
  `admin`은 `SERVICE_ROLE_KEY`로 만든 클라이언트 — **RLS를 완전히 우회**하므로, "누가 요청했는지"
  판단은 반드시 `caller()`로 따로 해야 한다(admin 클라이언트 자체는 아무나 다 보여준다).
  `login` 함수만 이 래퍼를 안 쓴다(비로그인 상태에서 불러야 하니까).
- **`llm.ts`** — Anthropic·OpenAI·Google·Groq 4사 어댑터. 프롬프트 구성·JSON 파싱은 공용이고,
  제공사별로 다른 건 "요청을 보내고 텍스트를 받는" 함수 하나뿐이다. `Deno.env.get()`으로 키가
  있는 제공사만 `availableModels()`에 노출된다 — 지금은 `GOOGLE_API_KEY`만 등록돼 있다. Groq는
  무료 티어로 오픈소스 모델(Llama 등)을 OpenAI 호환 `chat/completions` 형식으로 호출한다
  (`GROQ_API_KEY` 등록 전까지는 선택지에 안 뜬다).

### 4.2 함수별 요약

| 함수 | 하는 일 | 특이점 |
|---|---|---|
| `login` | 아이디/이메일 로그인 프록시 | 유일하게 인증 없이 호출 가능(3.5절) |
| `profile-extract` | 자기소개 → 기술 스택·관심사·역할 태그 | 참가자당 1회, 실패해도 예외를 밖으로 안 던짐 |
| `analyze-submission` | 저장소 사전 분석(심사 보조) | `EdgeRuntime.waitUntil`로 응답 뒤 백그라운드 실행(4.3절) |
| `github` | 저장소 프록시(repo/readme/tree/file) | `github_cache` 테이블에 30분 캐싱, 토큰으로 5000회/시간 확보 |
| `recommendations` | 팀 추천 | **LLM 안 씀** — 규칙 기반 점수만(5장) |

### 4.3 응답 후 계속 실행하기 — `EdgeRuntime.waitUntil`

`analyze-submission`은 저장소를 최대 30개 파일·12만 자까지 읽어 LLM에 넘긴다 — 벽시계
150초에 걸릴 수 있는 일이다. 그런데 클라이언트를 150초 동안 기다리게 하는 대신:

1. 요청이 오면 "분석 중" 행을 즉시 만들고 응답을 바로 돌려준다.
2. 실제 분석은 `EdgeRuntime.waitUntil(promise)`로 예약한다 — 응답이 나간 뒤에도 그 Promise가
   끝날 때까지 함수 인스턴스가 살아 있는다.
3. 화면은 그 행을 폴링해서 완료되면 보여준다.

병렬화도 같이 쓴다 — 파일을 순차로 읽으면 저장소 하나에 8분씩 걸린 적이 실제로 있었다
(예전 Django 시절, `docs/REFERENCE.md` 참고). 지금은 `PARALLEL = 6`으로 동시에 읽는다.
**대회 전체를 한 번에 분석하지 않고 제출물마다 함수를 따로 호출**하는 것도 같은 이유다 —
한 호출에 다 몰면 그 호출 하나가 150초 제한에 걸린다.

## 5. AI 활용 설계

**원칙: 대회 당일 요청 경로에는 LLM을 넣지 않는다.** 허용되는 자리는 셋뿐이다 — (a)
참가자당 1회·짧은 입력(프로필 추출), (b) 운영자가 시점을 고르는 일괄 실행(저장소 분석),
(c) 대회 종료 후(현재 미구현, 보류 중인 참가자 피드백 다이제스트). 이 표 밖의 아이디어는
전부 탈락시켰다 — 판단 근거와 기각한 항목(자동 채점, LLM 팀 매칭, 표절 탐지 등)의 전체
목록은 [docs/REFERENCE.md 2.2절](docs/REFERENCE.md)에 있다.

**제공사 추상화가 왜 이렇게 생겼는지:** `_shared/llm.ts`가 프롬프트 구성과 JSON 파싱을
공용으로 두고 제공사 어댑터만 갈아끼우게 만든 이유는, 나중에 모델을 바꿔 비교할 때 **프롬프트
차이가 아니라 순수한 모델 차이만** 보고 싶어서다. 이 구조 덕분에 지금 `GOOGLE_API_KEY` 하나로
전 구간이 돌고 있고, 다른 제공사 키를 등록하면(`supabase secrets set` + `functions deploy`)
코드 변경 없이 선택지에 추가된다 — Groq(`GROQ_API_KEY`)가 그 예로, 무료 티어로 오픈소스
모델을 붙일 수 있다.

**점수를 절대 제안하지 않는다** — `analyze-submission`의 프롬프트가 명시적으로 "점수를
매기거나 제안하지 마라"를 첫 규칙으로 건다(`supabase/functions/analyze-submission/index.ts`).
제안 점수가 뜨면 심사위원이 거기 닻을 내려(anchoring) 결국 모델이 채점하는 것과 같아지기
때문이다 — 기능이 아니라 **대회 정당성 문제**로 다룬다.

## 6. 로컬 개발 환경

Docker Desktop 필요. 전부 `npx --yes supabase@latest ...`로 실행(전역 설치 불필요).

```bash
npx supabase start          # Postgres, PostgREST, Auth, Realtime, Studio, Mailpit 컨테이너 기동
npx supabase status -o json # ANON_KEY / SERVICE_ROLE_KEY 확인
npx supabase db reset       # migrations/ 전부 재적용 + seed. 스키마를 고쳤으면 이걸로 반영
npx supabase migration up   # 새 마이그레이션 파일만 적용(reset 없이)
```

로컬 포트: API `54321`, Postgres `54322`, Studio `54323`, Mailpit(받은 메일함) `54324`.

**Edge Functions 로컬 실행:**

```bash
npx supabase functions serve            # 전체 함수, 로컬 DB에 연결
npx supabase functions serve login      # 함수 하나만
```

키가 필요한 함수는 `supabase/functions/.env`에 넣는다(커밋 금지, `.gitignore` 처리됨).

## 7. 테스트

**pgTAP — DB 레이어(RLS·트리거·RPC) 전체:**

```bash
npx supabase test db
```

`supabase/tests/*.sql`. 로그인 사용자를 흉내 내는 패턴이 거의 모든 테스트에 반복된다:

```sql
-- supabase/tests/02_scoring.test.sql
select set_config('request.jwt.claims', '{"sub":"...uuid...","role":"authenticated"}', true);
set local role authenticated;
select is((select username from public.assign_judge('c1', 'judge1')), 'judge1', '...');
select throws_ok($$ select public.assign_judge('c1', 'nobody') $$, 'P0002', '...', '...');
reset role;
```

PostgREST가 실제로 하는 일(JWT의 `sub`를 `auth.uid()`로 노출하고 세션 역할을 `authenticated`로
바꾸는 것)을 테스트 안에서 직접 흉내 낸다 — 그래서 이 테스트들이 **RLS 정책까지 실제로
타면서** 검증한다(그냥 함수만 호출하는 것과 다르다).

**Deno 테스트 — Edge Function 순수 로직:**

`supabase/functions/_shared/logic_test.ts` 같은 파일. `deno test`로 실행하며, LLM 응답 파싱·
경로 필터링처럼 네트워크가 필요 없는 부분만 다룬다(네트워크 호출 자체는 이 계층에서 테스트
안 함 — 실제 API 호출은 비용이 들고 비결정적이라서).

## 8. 배포

```bash
npx supabase login                              # 브라우저로 인증(대화형, 직접 실행)
npx supabase link --project-ref <프로젝트 ref>
npx supabase db push                            # migrations/ 를 프로덕션에 적용
npx supabase secrets set --env-file <키 파일>    # GOOGLE_API_KEY, GITHUB_TOKEN 등
npx supabase functions deploy                   # 전체, 또는 함수명 하나만 지정
```

`SUPABASE_URL`·`SUPABASE_SERVICE_ROLE_KEY`·`SUPABASE_ANON_KEY`는 Edge Function 런타임이
자동으로 주입한다 — `secrets set`으로 넣을 필요 없다(넣어도 무해하지만 불필요).

**Auth 대시보드 설정(코드 밖, 웹에서):** SMTP(비밀번호 재설정 메일 발송 — 기본 메일은 시간당
2통·팀원 전용이라 운영에는 못 씀), Site URL·Redirect URL.

**배포 후 확인 (핵심만 — 매번 반복):**

```bash
# anon 키로 직접 호출해 RLS가 실제로 막는지 확인 (401/403 기대)
curl "$SUPABASE_URL/rest/v1/awards?select=id" -H "apikey: $ANON_KEY"
# 새 함수가 인증 가드를 타는지 확인 (401 "로그인이 필요합니다" 기대)
curl -X POST "$SUPABASE_URL/functions/v1/<함수명>" -H "apikey: $ANON_KEY"
```

비밀번호가 필요한 로그인 자체는 사람이 브라우저에서 직접 확인한다(아이디 로그인, 이메일
로그인, 틀린 비밀번호와 존재하지 않는 아이디가 같은 오류를 내는지).

## 9. 트러블슈팅

### Windows에서 Docker Postgres가 54322(또는 5432)에 바인딩 안 됨

Windows는 동적 포트 범위를 예약해 특정 포트를 통째로 못 쓰게 만들 수 있다.

```powershell
netsh int ipv4 show excludedportrange protocol=tcp
```

여기 걸린 대역과 `supabase/config.toml`의 포트가 겹치면 실패한다. `config.toml`에서 포트를
바꾸거나(예: 5430~5529가 예약이면 5432도 5433도 못 씀), Windows 예약을 확인해 겹치지 않는
대역을 고른다.

### 심사 보조 분석이 "JSON을 찾지 못했습니다"로 실패

원인 두 가지가 겹칠 수 있다: (1) 모델이 JSON 앞뒤에 설명 문장을 붙임 — 파서가 첫 `{`부터
마지막 `}`까지만 잘라내게 해야 한다(`_shared/logic.ts`의 `parseJsonObject`). (2) 출력 토큰
상한이 너무 낮아 본문이 잘림 — 저가·추론 모델은 본문 전에 추론에 토큰을 먼저 쓰므로, 상한이
빠듯하면 본문이 아예 안 나온다(`analyze-submission/index.ts`의 `MAX_OUTPUT_TOKENS` 참고).

### db push 전에 꼭 확인할 것

`supabase migration list`로 로컬/원격 마이그레이션 목록을 먼저 비교한다. 프로덕션이 로컬과
다른 이력을 갖고 있으면(예: Studio에서 직접 스키마를 고친 적이 있으면) `db push`가 충돌한다.

## 10. 남은 일

- **롤백 창 종료 처리 (예정보다 앞당김, 2026-09-19):** `backend/` 디렉터리 제거 완료.
  Render 서비스 삭제 완료(2026-09-23). 남은 것 — Django 테이블 백업 후 drop(데이터 이전
  완전 검증 후 진행 — 아직 미실행).
- **모바일 대응: 반응형 웹으로 결정 (네이티브 앱 안 감).** 목업(`design/mobile-ui/`)은
  참고용 스냅샷일 뿐, 실제 앱은 그 목업의 탭 구조(홈/갤러리/제출현황/프로필)를 그대로
  따르지 않는다 — 실앱은 대회 목록→상세→갤러리/제출물의 중첩 구조라 하단 탭바 자체가
  안 맞음. 2026-09-23 감사 결과: viewport meta는 이미 있었고, 카드 그리드
  (`.contest-list`/`.tile-grid`/`.project-body`)와 스코어보드 테이블(`.table-scroll`)도
  이미 640px/900px 브레이크포인트로 반응형이었음. 실제로 깨진 건 헤더
  (`.site-header`) 하나 — 390px에서 로고+브레드크럼+아바타+이름/역할+로그아웃이 68px
  고정 높이 한 줄에 안 들어가 가로 스크롤 발생 → `style.css`에 640px 미디어 쿼리로
  줄바꿈 허용 + `.auth-role` 숨김 처리해 수정 완료. 새 CSS 프레임워크·새 브레이크포인트
  체계·PWA는 붙이지 않음(YAGNI). 남은 것: 실기기(또는 DevTools 반응형 모드)로 전체
  화면 훑어보며 이번에 못 잡은 개별 컴포넌트 깨짐 있는지 확인.
- **SMTP:** 지금은 Resend의 테스트 발신 주소(`onboarding@resend.dev`)라 스팸함으로 갈 수
  있다. 사설 도메인이 생기면 발신 주소만 교체.
- **AI 후보(보류 중):** 참가자 피드백 다이제스트(심사 코멘트 → 참가자 요약, 대회 종료 후
  실행이라 제약 충돌 없음), 모델 비교 실험(Anthropic·OpenAI 키가 생기면). 상세는
  [docs/REFERENCE.md 2.2절](docs/REFERENCE.md).
- **심사위원 점수 편차 보정:** 통계로 풀 문제지만 실사용 심사위원이 3~4명이라 표본 부족으로
  보류. 심사위원 5명 이상 규모가 되면 재검토.
