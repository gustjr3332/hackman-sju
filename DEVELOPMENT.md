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

**함정 — 연쇄 삭제는 자식 쪽 게이트를 비켜 간다 (2026-09-24 발견·수정,
`20260924120000_lock_results_after_close.sql`):** 처음엔 팀 삭제에 게이트가 없고 제출물
게이트에만 기대고 있었다. 그런데 팀을 지우면 FK CASCADE가 제출물을 지우는 시점에는 부모(팀)
행이 이미 지워져 보이지 않아서, 제출물 게이트가 대회를 못 찾고(`v_status is null`) 통과했다.
결과적으로 팀원이 종료된 대회의 자기 팀을 지우면 제출물·점수까지 사라졌다. 교훈: **지워질 수
있는 가장 바깥 행(팀)에 게이트를 건다.** 같은 성질을 거꾸로 이용한 것도 있다 — 운영자가 대회
자체를 지울 때는 대회 행이 안 보이므로 `ensure_status`와 `guard_judge_delete`가 통과해 연쇄
삭제가 끝까지 간다(이전엔 점수가 있는 대회를 못 지웠다). 같은 마이그레이션에서 점수 삭제도
채점과 같은 "심사중" 조건으로 묶었다. 검증은 `supabase/tests/05_lock_after_close.test.sql`.

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

테스트는 빈 DB를 가정한다. 로컬 DB에 개발용 데이터(같은 아이디의 사용자 등)가 있으면
fixture가 겹쳐 실패하는데, `db reset` 대신 각 파일을 트랜잭션 안에서 기존 데이터를 지운 뒤
돌리고 롤백하면 데이터를 보존한 채 확인할 수 있다:

```bash
{ echo "begin; delete from public.contests; delete from auth.users;"; \
  sed -e '/^begin;$/d' -e '/^rollback;$/d' supabase/tests/05_lock_after_close.test.sql; echo "rollback;"; } \
  | docker exec -i supabase_db_hackman-sju psql -U postgres -X -q -At
```

**새 DB의 API 권한 확인(2026-10-30 Supabase 기본값 변경 대비):** 지금 로컬 DB는 옛 기본값이라
권한이 빠진 마이그레이션도 통과한다. 새 테이블·뷰를 추가했으면 새 기본값에서 한 번 돌려 본다.
`supabase/` 폴더(config.toml·migrations·tests)를 임시 폴더에 복사 → config.toml의
`project_id`를 바꾸고 포트 `543xx`를 `553xx`로 → 맨 앞에 다음 마이그레이션을 하나 넣는다:

```sql
alter default privileges for role postgres in schema public
  revoke select, insert, update, delete on tables from anon, authenticated, service_role;
```

그다음 `npx supabase start --workdir <임시 폴더>` → `npx supabase test db --workdir <임시 폴더>`
→ 끝나면 `npx supabase stop --no-backup --workdir <임시 폴더>`. 권한을 빠뜨린 테이블이 있으면
해당 테스트가 `permission denied`로 실패한다.

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
  완전 검증 후 진행 — 아직 미실행). 운영 DB의 `auth_user` 등에 옛 계정의 이메일·비밀번호
  해시가 남아 있어서, 아래 "계정 삭제" 기능과 개인정보처리방침보다 **먼저** 끝내야 한다.
- **운영 반영 대기 — 마이그레이션 2개.** 운영은 커밋 후 사람이 직접 `npx supabase db push`.
  - `20260924120000_lock_results_after_close.sql`: 팀 삭제는 모집중·진행중만, 점수 삭제는
    심사중만, 점수 있는 대회도 운영자가 삭제 가능. 로컬 적용·pgTAP 129건 통과.
  - `20260924130000_explicit_data_api_grants.sql`: Supabase가 2026-10-30부터 public의 새
    테이블·뷰에 API 권한을 자동으로 안 주는 변경 대응(안내 메일, [changelog](https://supabase.com/changelog/45329-breaking-change-tables-not-exposed-to-data-and-graphql-api-automatically)).
    지금까지 자동으로 붙던 권한에서 schema.sql이 거둬들인 것을 뺀 나머지를 명시한다 — 운영·기존
    로컬에서는 아무것도 안 바뀌고, DB를 새로 만들 때(db reset·새 프로젝트·프리뷰 브랜치)만 효과가
    있다. 검증: 새 기본값을 흉내 낸 별도 로컬 스택(아래 7절)에서 이 파일 없이는 API가
    `permission denied`·pgTAP 실패, 넣으면 129건 통과이고, 테이블·열·함수 권한 목록이 기존 DB와
    완전히 같음(45·122·26행).
- **모바일 대응: 반응형 웹으로 결정 (네이티브 앱 안 감).** 목업(`design/mobile-ui/`)은
  참고용 스냅샷일 뿐, 실제 앱은 그 목업의 탭 구조(홈/갤러리/제출현황/프로필)를 그대로
  따르지 않는다 — 실앱은 대회 목록→상세→갤러리/제출물의 중첩 구조라 하단 탭바 자체가
  안 맞음. 2026-09-23 감사 결과: viewport meta는 이미 있었고, 카드 그리드
  (`.contest-list`/`.tile-grid`/`.project-body`)와 스코어보드 테이블(`.table-scroll`)도
  이미 640px/900px 브레이크포인트로 반응형이었음. 실제로 깨진 건 헤더
  (`.site-header`) 하나 — 390px에서 로고+브레드크럼+아바타+이름/역할+로그아웃이 68px
  고정 높이 한 줄에 안 들어가 가로 스크롤 발생 → `style.css`에 640px 미디어 쿼리로
  줄바꿈 허용 + `.auth-role` 숨김 처리해 수정. 이 상태로도 긴 아이디("김지현길고긴이름
  테스트" 같은 12자)에서는 `header-right`(테마토글+아바타+이름+로그아웃)만으로 390px를
  넘어 재차 가로 스크롤 나는 걸 DevTools에서 폭 강제 오버라이드로 확인 →
  `.auth-name`에 96px 말줄임표(ellipsis) 추가해 해결. 이어서 로컬 Supabase(도커) 띄우고
  실데이터로 대회 상세/스코어보드/갤러리까지 마저 스윕 — 스코어보드
  테이블(`.scoreboard-table`)에서 `width:100%` 때문에 좁은 화면에서 "평균 점수"
  헤더와 팀/제출물 이름이 글자 단위로 세로 줄바꿈되던 것 발견 → `th`/`td`에
  `white-space: nowrap` 추가해 테이블이 자연스럽게 넓어지고 `.table-scroll`의
  `overflow-x:auto`로 옆 스크롤되게 수정 (2026-09-23). 대회 목록·로그인·상세·갤러리
  화면 모두 390px에서 가로 스크롤·글자 단위 줄바꿈 없음 확인 완료. 새 CSS
  프레임워크·새 브레이크포인트 체계·PWA는 붙이지 않음(YAGNI).
  **마무리(2026-09-24):** 목업의 *구조*만 640px 이하에 이식, 색·서체·라운드는 기존
  토큰 유지. ① 헤더 → 앱 바(안쪽 화면은 뒤로 + 제목, 이름·역할·로그아웃은 아바타를
  누르면 펼치는 계정 메뉴로, sticky) ② 대회 상세에 섹션 바로가기 탭(스크롤 이동, 앱 바
  밑에 sticky) ③ "제출물 둘러보기"를 화면 아래 고정 액션 바로 ④ 스코어보드는 제출물
  열을 팀 이름 아래로 접어 4열 ⑤ 640px 이하·터치 기기에서 버튼 최소 44px ⑥ 스타일
  없는 입력창까지 16px(iOS 확대 방지) ⑦ `viewport-fit=cover` + safe-area 여백.
  하단 탭바는 여전히 안 씀(위 이유 그대로). 390px 헤드리스 크롬으로 목록·상세·갤러리·
  로그인·계정 메뉴 확인: 가로 스크롤 0, 16px 미만 입력창 0, 44px 미만은 테마 토글
  (규격상 36px)과 문장 속 링크뿐. 1280px에서 데스크톱 레이아웃 변화 없음도 확인.
- **스토어 배포 준비 (2026-09-24 시작):** 스토어에 올리기로 해서 위의 "PWA는 YAGNI"
  판단을 뒤집었다. 들어간 것 — `frontend/public/manifest.webmanifest`, 아이콘
  (`public/icons/`, 헤더 로고 마크를 IBM Plex Mono "H"로 렌더), 서비스 워커
  (`public/sw.js`: 화면 이동은 네트워크 우선·끊기면 보관본, `/assets/`는 보관본 우선,
  Supabase 등 다른 출처는 손대지 않음, 새 index.html을 받을 때 옛 번들 정리),
  `index.html`의 theme-color(시스템 라이트/다크에 따라 `--paper`), 오프라인 안내
  줄과 재연결 시 목록·상세 자동 새로고침. `vite preview`에서 크롬 설치 가능 판정 오류
  0건, 오프라인 새로고침·딥링크에서도 앱 화면 뜨는 것 확인. 스토어 제출용 아이콘은
  `design/store/`(Play 512, App Store 1024). 새 테이블(신고·차단)을 만들면 2026-10-30
  이후 Supabase가 GRANT를 자동으로 주지 않으므로 같은 마이그레이션에 직접 적는다(모양은
  `20260924130000_explicit_data_api_grants.sql` 참고).
  남은 일 (정책 기준일 2026-09-24, 제출 직전 링크 재확인):
  - 먼저 정할 것
    - [x] 포장 방식 — 두 스토어 모두 출시. 안드로이드는 TWA(Bubblewrap), iOS는 Capacitor +
      푸시 알림 같은 앱 기능 1개 이상(심사 4.2 "웹사이트 재포장" 거절 대비) (2026-09-24 결정)
    - [ ] Play 계정 유형 — 개인이면 테스터 12명이 14일 연속 참여해야 출시 가능
      ([요건](https://support.google.com/googleplay/android-developer/answer/14151465)).
      조직 계정은 면제지만 D-U-N-S 필요
    - [x] 탈퇴 시 데이터 처리 — 계정·이메일·프로필 삭제, 팀 참가·제출물·점수는
      "탈퇴한 사용자"로 익명화 (2026-09-24 결정, 구현 방법은 아래 "권한 검토")
    - [ ] 도메인·패키지명 — 이번 주 안에 `.com` 도메인으로 바꾸기로 함(확정되면 알려 주기로).
      assetlinks는 도메인에, 패키지명(안드로이드)·Bundle ID(iOS)는 앱에 영구히 묶이므로
      둘 다 새 도메인의 역도메인(예: `com.<도메인>.app`)으로 정한다. 확정되면 할 일:
      `frontend/capacitor.config.ts`의 `appId`와 `ios/App/App.xcodeproj`의
      `PRODUCT_BUNDLE_IDENTIFIER`(현재 임시값 `com.example.hackman`) 교체, Vercel 도메인 연결,
      Supabase Auth Site URL 교체, 안드로이드 TWA 생성(아래)
  - 앱 안에 만들 것 (없으면 두 스토어 모두 거절 사유)
    - [ ] 계정 삭제: 계정 메뉴 버튼 + 로그인 없이 요청하는 웹 페이지 (반나절~1일,
      Apple 5.1.1(v), [Play 요건](https://support.google.com/googleplay/android-developer/answer/13327111))
    - [ ] 개인정보처리방침 페이지 + 앱 안 링크. 처리 위탁·국외 이전(Supabase, Vercel,
      Resend, LLM 제공사) 포함 (2~3시간, Apple 5.1.1(i))
    - [ ] 신고·차단·연락처 공개 — 팀 이름·제출물·프로필이 남에게 보이는 콘텐츠라서
      (약 1일, Apple 1.2)
    - [ ] 심사용 데모 계정 + 샘플 대회, 운영 환경에 (1시간, Apple 2.1(a))
    - [ ] Supabase Auth Site URL·Redirect URLs를 운영 도메인으로 (30분)
    - [ ] 이용약관 페이지 (권장, 1시간)
  - 안드로이드 (Google Play) — TWA는 웹 주소를 그대로 띄우므로 **도메인 확정 + 새 도메인에
    PWA 배포**가 끝나야 만들 수 있다. 이 PC에는 JDK·Android SDK가 없다(2026-09-24 확인).
    - [ ] Play Console 개인 계정: 25달러 1회, 신원 확인, Android 10+ 실기기 인증
    - [ ] TWA 패키지 생성, targetSdk 36 이상
      ([요건](https://support.google.com/googleplay/android-developer/answer/11926878)).
      질문에 답하는 대화형이라 직접 실행: `cd frontend && npx @bubblewrap/cli init
      --manifest https://<도메인>/manifest.webmanifest --directory android` → 처음 실행 때
      JDK 17·Android SDK를 받을지 물으면 "예"(`~/.bubblewrap`에 설치). 패키지명은 위 규칙,
      서명 키 위치는 저장소 밖. 이어서 `npx @bubblewrap/cli build`
    - [ ] 업로드 키를 저장소 밖에 보관, Play 앱 서명 사용
    - [ ] `frontend/public/.well-known/assetlinks.json` (없으면 앱 위에 주소창이 보임)
    - [ ] 등록정보: 짧은 설명 80자, 전체 설명, 그래픽 1024×500, 휴대전화 스크린샷 2장+
    - [ ] 앱 콘텐츠: 개인정보처리방침 URL, 데이터 보안 양식(계정 삭제 URL), 콘텐츠 등급,
      타깃 연령, 앱 액세스(데모 계정)
    - [ ] 비공개 테스트 14일 → 프로덕션 액세스 신청 → 출시 심사
  - iOS (App Store)
    - [x] Capacitor 8 iOS 프로젝트 (`frontend/ios/`, Swift Package Manager라 CocoaPods 불필요)
      — 웹 빌드(dist)를 앱 안에 넣는 방식. 앱 아이콘(1024, 알파 없음)과 실행 화면(라이트·다크,
      헤더 로고 마크)을 HACKMAN 것으로 교체, `ITSAppUsesNonExemptEncryption=false`(HTTPS만
      쓰므로 업로드마다 수출 규정 질문을 건너뜀). 웹 쪽 앱 대응: 서비스 워커는 앱
      (`capacitor://`)에서 지원되지 않아 등록이 실패하고 무시됨, 비밀번호 재설정 메일 링크는 `VITE_SITE_URL`로 웹 주소에
      돌아오게 함. (2026-09-24)
    - [x] 앱 빌드 명령 `npm run ios:sync` (= `vite build --mode ios` + `cap sync ios`).
      `frontend/.env.ios.local`(커밋 안 됨)에 운영 `VITE_SUPABASE_URL`·`VITE_SUPABASE_ANON_KEY`·
      `VITE_SITE_URL`을 넣어야 하고, 빠지거나 https가 아니면 빌드가 멈춘다(로컬 Supabase를
      가리키는 앱이 심사에 올라가는 사고 방지, `vite.config.ts`).
    - [ ] Apple Developer Program 연 129,000원
    - [ ] Mac + Xcode 26에서 빌드·서명·업로드 (2026-04-28부터 iOS 26 SDK 필수). 이 PC는
      Windows라 여기서 못 한다 — Mac을 빌리거나 GitHub Actions macOS 러너로 클라우드 빌드.
      Mac에서: `cd frontend && npm ci && npm run ios:sync && npx cap open ios` → Xcode에서
      Signing 팀 선택 → Product › Archive → Distribute
    - [ ] 앱다운 기능: 푸시 알림(발표 차례·채점 시작) 2~3일 — APNs 키, 기기 토큰 저장 테이블
      (10/30 이후 GRANT 직접), 발송 Edge Function
    - [ ] iPad 지원 여부 — 지금 설정은 iPhone·iPad 둘 다(`TARGETED_DEVICE_FAMILY = "1,2"`).
      유지하면 13인치 iPad 스크린샷도 필요, 빼면 iPhone만
    - [ ] 도메인 확정 후 Universal Links(메일 링크가 앱에서 열리게) — 선택
    - [ ] App Store Connect: 개인정보 라벨, 새 연령 등급 설문, 6.9인치 스크린샷, 데모 계정
  - 가장 빠른 안드로이드 일정: 공통 코드 2~3일 → 비공개 테스트 14일 → 심사 며칠.
    9/24 시작 기준 빠르면 10월 셋째~넷째 주.
- **권한 검토 후속 (2026-09-24):** 역할별 권한은 RLS·트리거·RPC가 강제하고 있어 큰 틀은
  괜찮다. 남은 것:
  - [ ] **탈퇴 익명화 마이그레이션.** 지금은 `auth.users → profiles → participants·judges →
    scores`가 전부 CASCADE라, 채점한 심사위원은 계정 삭제가 실패하고(보호 트리거) 참가자는
    팀 기록에서 지워진다(로컬에서 롤백으로 확인). 고칠 것: `participants.user_id`,
    `judges.user_id`를 NULL 허용 + `ON DELETE SET NULL`, `team_list`·`judge_list`·`score_list`
    뷰에 `coalesce(username_of(...), '탈퇴한 사용자')`, 탈퇴 Edge Function(본인 확인 →
    모집중 대회의 팀 참가는 삭제 → 마지막 운영자면 거절 → `auth.admin.deleteUser`).
    선행: 위 Django 테이블 drop.
  - [ ] 개인정보처리방침에 옮길 문장(초안): 탈퇴하면 계정(아이디·이메일·비밀번호)과
    프로필은 바로 삭제된다. 참가한 대회의 팀 참가 기록·팀 제출물·심사 점수와 코멘트는 대회
    결과 보존을 위해 남고 작성자는 "탈퇴한 사용자"로 표시된다. 모집 중인 대회의 팀 참가는
    함께 삭제된다. LLM 제공사에 보낸 자기소개는 제공사 보관 정책을, 백업·접속 기록은 보관
    기간을 따른다(기간은 요금제 확인 후 숫자로).
  - [ ] 제출물 insert 시 `submitted_at`을 클라이언트가 정할 수 있음 → 제출 시각순 발표
    배정에서 앞자리를 받는다. `revoke insert on submissions` 후 필요한 열만 grant.
  - [ ] 팀 insert 시 `presentation_*` 열 지정 가능("발표 중" 표시 위조). `grant insert
    (contest_slug, name)`만 남기기.
  - [ ] `github` 함수를 로그인한 누구나 임의 저장소로 부를 수 있음(서버 토큰 한도 소모).
    낮음 — 호출 한도나 등록된 저장소만 허용.
  - [ ] Claude Code 권한 규칙 적용 여부: 지금 전역·프로젝트 모두 allow/deny/ask 0개(auto
    모드 분류기만). 제안 — `.claude/settings.json`의 deny에 `npx supabase db push`,
    `link`, `migration repair`, `secrets set`, `functions deploy`, ask에 `git push`,
    `npx supabase db reset` (PowerShell 형태도 같이).
  - 앱 안 LLM(Claude 포함 4사)은 DB에 직접 닿지 않는다. 받는 것은 본인 자기소개(프로필 자동
    정리)와, 운영자가 실행한 경우 제출물 제목·설명·README·코드 파일 최대 30개뿐. 개인정보
    처리방침의 국외 이전 항목에 이대로 적는다.
- **디자인 방향 (2026-09-24 조사):** 시가총액 상위 100개 IT기업 홈페이지 99곳을
  헤드리스 크롬으로 실측해 비교했다. 추천은 **A. 현 토큰 유지 + 서체만 교정**:
  Space Grotesk·IBM Plex Mono에 한글 글리프가 없어 한글이 OS 서체로 떨어지고, 모노 영역의
  한글 라벨("평균 점수", "실시간 반영", "10분 예정")이 벌어져 보인다 → UI 서체 Pretendard,
  모노는 숫자만, 모바일 루트 19→17px, 본문 폭 860→1080px(REFERENCE.md와 맞춤). 반나절~1일.
  대안 B(목업 시각 언어: 둥근 카드·알약 칩·어두운 카운트다운 카드, 인디고 대신 코발트,
  2~3일, REFERENCE.md "하지 않는 것" 1·5번 폐기 필요), C(Carbon 계열 콘솔: 각진 모서리,
  IBM Plex Sans KR, 1.5~2일). 실측 요약: 버튼 모양 각짐 27·알약 23·약간 둥긂 19(71곳),
  헤드라인 전용 서체 55/90, OS 다크 모드 반응 1/99, 모바일 하단 탭바 7/99.
  - [x] **B안 채택, 앱(640px 이하·iOS 앱)에 적용 (2026-09-24).** 규칙은 REFERENCE.md
    "모바일 시각 규칙 — B안", 구현은 `style.css` 끝 "모바일 B안" 블록. Pretendard(npm, 번들
    포함, 쓰는 글자 조각만 받음), 토큰 재정의(라이트·다크), 라운드 토큰화(`--r` 컨트롤,
    `--r-card` 면 — 넓은 화면은 둘 다 6px 그대로), 상태 알약 칩, 진행 중 대회·카운트다운을
    어두운 히어로 카드 + 시간·분·초 타일(`CountdownTimer`에 타일 마크업 추가, 넓은 화면은
    숨김), 칩 탭, 섹션 제목 레일 제거, 원형 아바타. theme-color·manifest·iOS 실행 화면 색도
    B안 바탕으로. 390px 라이트·다크로 목록·상세·갤러리 확인(가로 스크롤 0, Pretendard 적용),
    1280px은 Space Grotesk·6px 그대로 확인.
  - [x] **넓은 화면까지 B안 전체 적용 (2026-09-25, 선택지 1~3 중 2번).** 모바일 전용 블록을 없애고
    토큰(`:root`·다크 2곳)을 B안 값으로 바꾼 뒤, 칩·히어로·원형 아바타 규칙을 기존 규칙 자리에
    합쳤다(덮어쓰기 층 없음). 모노 서체와 Google Fonts 링크 제거, 카운트다운은 타일 하나로
    통일(시계 마크업 삭제), 본문 폭 860→1080px(REFERENCE.md와 맞춤). REFERENCE.md 1절을 B안
    값으로 개정. 1280px·390px × 라이트·다크로 목록·상세·갤러리 확인(가로 스크롤 0).
- **SMTP:** 지금은 Resend의 테스트 발신 주소(`onboarding@resend.dev`)라 스팸함으로 갈 수
  있다. 사설 도메인이 생기면 발신 주소만 교체.
- **AI 후보(보류 중):** 참가자 피드백 다이제스트(심사 코멘트 → 참가자 요약, 대회 종료 후
  실행이라 제약 충돌 없음), 모델 비교 실험(Anthropic·OpenAI 키가 생기면). 상세는
  [docs/REFERENCE.md 2.2절](docs/REFERENCE.md).
- **심사위원 점수 편차 보정:** 통계로 풀 문제지만 실사용 심사위원이 3~4명이라 표본 부족으로
  보류. 심사위원 5명 이상 규모가 되면 재검토.
