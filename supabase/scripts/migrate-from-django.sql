-- Django 테이블(auth_user, contests_*) → 새 스키마. 전환 때 한 번만 돈다.
--
-- 같은 데이터베이스의 public 스키마에 두 벌이 같이 있으므로 SQL 하나로 옮긴다. 한 트랜잭션이라
-- 중간에 실패하면 아무것도 남지 않는다.
--
--   미리보기(끝에서 롤백):  psql "$DB_URL" -v ON_ERROR_STOP=1 -f supabase/scripts/migrate-from-django.sql
--   실제 반영:               psql "$DB_URL" -v ON_ERROR_STOP=1 -v apply=1 -f supabase/scripts/migrate-from-django.sql
--
-- DB_URL 은 postgres 사용자 접속 문자열이다(Render 의 DATABASE_URL 과 같은 것). 테이블 트리거를
-- 잠시 끄므로 테이블 소유자여야 한다.
--
-- 옮기는 방식
-- - 계정: auth.users 에 직접 넣는다. 비밀번호는 비워 둔다. Django 해시(PBKDF2)는 Supabase Auth 가
--   읽지 못하므로 전원이 '비밀번호를 잊으셨나요?'로 새로 정한다. 이메일이 없는 계정이 하나라도
--   있으면 멈춘다. Django admin 에서 이메일을 채우고 다시 돌린다.
-- - id: 새 테이블의 id 는 identity always 지만 overriding system value 로 Django id 를 그대로
--   쓴다. 옮긴 뒤 시퀀스를 최댓값 다음으로 맞춘다.
-- - 상태별 제한 트리거(심사중에만 점수 등)는 과거 데이터에 맞지 않으므로 이 트랜잭션 동안만
--   끈다(disable trigger user). 외래키·check 제약은 그대로 검사된다.

\set ON_ERROR_STOP 1
begin;

create function pg_temp.txt(j jsonb) returns text[] language sql immutable
  as $$ select coalesce(array(select jsonb_array_elements_text(j)), '{}') $$;

do $$
declare v text;
begin
  select string_agg(username, ', ') into v from public.auth_user where coalesce(trim(email), '') = '';
  if v is not null then
    raise exception '이메일이 없는 계정: %. Django admin 에서 이메일을 채운 뒤 다시 실행하세요.', v;
  end if;
  select string_agg(e, ', ') into v from (
    select lower(trim(email)) e from public.auth_user group by 1 having count(*) > 1) d;
  if v is not null then
    raise exception '이메일이 겹치는 계정: %. 한 사람당 한 계정이어야 합니다.', v;
  end if;
end $$;

alter table public.contests disable trigger user;
alter table public.teams disable trigger user;
alter table public.participants disable trigger user;
alter table public.submissions disable trigger user;
alter table public.judges disable trigger user;
alter table public.scores disable trigger user;
alter table public.awards disable trigger user;
alter table public.profiles disable trigger user;
alter table public.submission_reviews disable trigger user;

-- 계정. handle_new_user 트리거(auth.users 에 걸림)가 프로필 행을 만든다.
create temp table user_map on commit drop as
  select id as django_id, gen_random_uuid() as uid from public.auth_user;

insert into auth.users (
  instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
  raw_app_meta_data, raw_user_meta_data, created_at, updated_at, last_sign_in_at, banned_until,
  confirmation_token, recovery_token, email_change_token_new, email_change,
  email_change_token_current, phone_change, phone_change_token, reauthentication_token)
select '00000000-0000-0000-0000-000000000000', m.uid, 'authenticated', 'authenticated',
  lower(trim(u.email)), '', now(),
  '{"provider": "email", "providers": ["email"]}',
  jsonb_build_object('username', u.username, 'email_verified', true),
  u.date_joined, now(), u.last_login, case when u.is_active then null else 'infinity'::timestamptz end,
  '', '', '', '', '', '', '', ''
from public.auth_user u join user_map m on m.django_id = u.id;

insert into auth.identities (provider_id, user_id, identity_data, provider, created_at, updated_at)
select m.uid::text, m.uid,
  jsonb_build_object('sub', m.uid::text, 'email', lower(trim(u.email)), 'email_verified', true),
  'email', now(), now()
from public.auth_user u join user_map m on m.django_id = u.id;

-- 스택 목록: Django admin 에서 고친 내용이 있으면 시드를 덮어쓴다.
insert into public.tech_stacks (slug, name, category, aliases, is_active)
select slug, name, category, pg_temp.txt(aliases), is_active from public.contests_techstack
on conflict (slug) do update set
  name = excluded.name, category = excluded.category, aliases = excluded.aliases, is_active = excluded.is_active;

update public.profiles p set is_staff = u.is_staff
from public.auth_user u join user_map m on m.django_id = u.id where p.id = m.uid;

update public.profiles p set
  intro = d.intro, github_url = d.github_url, skills = pg_temp.txt(d.skills),
  other_skills = pg_temp.txt(d.other_skills), interests = pg_temp.txt(d.interests),
  roles = pg_temp.txt(d.roles), level = d.level, looking_for_team = d.looking_for_team,
  extraction_status = d.extraction_status, extraction_error = d.extraction_error,
  extracted_by = d.extracted_by, extracted_at = d.extracted_at, updated_at = d.updated_at
from public.contests_profile d join user_map m on m.django_id = d.user_id where p.id = m.uid;

insert into public.contests (slug, name, description, status, start_at, end_at, presentation_minutes, created_at, updated_at)
select slug, name, description, status, start_at, end_at, presentation_minutes, created_at, updated_at
from public.contests_contest;

insert into public.teams (id, contest_slug, name, created_at, presentation_order, presentation_minutes,
  presentation_started_at, presentation_ended_at)
overriding system value
select id, contest_id, name, created_at, presentation_order, presentation_minutes,
  presentation_started_at, presentation_ended_at
from public.contests_team;

insert into public.participants (id, team_id, user_id, joined_at)
overriding system value
select p.id, p.team_id, m.uid, p.joined_at
from public.contests_participant p join user_map m on m.django_id = p.user_id;

insert into public.submissions (id, team_id, title, description, link_url, repo_url, submitted_at)
overriding system value
select id, team_id, title, description, link_url, repo_url, submitted_at from public.contests_submission;

insert into public.judges (id, contest_slug, user_id)
overriding system value
select j.id, j.contest_id, m.uid from public.contests_judge j join user_map m on m.django_id = j.user_id;

insert into public.scores (id, submission_id, judge_id, round, value, comment, created_at, updated_at)
overriding system value
select id, submission_id, judge_id, round, value, comment, created_at, updated_at from public.contests_score;

insert into public.awards (id, contest_slug, rank, title)
overriding system value
select id, contest_id, rank, title from public.contests_award;

insert into public.submission_reviews (id, submission_id, provider, model, status, summary, findings,
  cited_paths, stack, truncated, files_read, input_tokens, output_tokens, error, submission_seen_at,
  created_at, updated_at)
overriding system value
select id, submission_id, provider, model, status, summary, findings, pg_temp.txt(cited_paths),
  pg_temp.txt(stack), truncated, files_read, input_tokens, output_tokens, error, submission_seen_at,
  created_at, updated_at
from public.contests_submissionreview;

select setval(pg_get_serial_sequence('public.' || t, 'id'),
  coalesce((xpath('/row/m/text()', query_to_xml('select max(id) m from public.' || t, false, true, '')))[1]::text::bigint, 0) + 1,
  false)
from unnest(array['teams', 'participants', 'submissions', 'judges', 'scores', 'awards', 'submission_reviews']) t;

alter table public.contests enable trigger user;
alter table public.teams enable trigger user;
alter table public.participants enable trigger user;
alter table public.submissions enable trigger user;
alter table public.judges enable trigger user;
alter table public.scores enable trigger user;
alter table public.awards enable trigger user;
alter table public.profiles enable trigger user;
alter table public.submission_reviews enable trigger user;

-- 건수 대조. 하나라도 어긋나면 전체를 되돌린다.
create temp table counts on commit drop as
select * from (values
  ('users',        (select count(*) from public.auth_user),                 (select count(*) from public.profiles p join user_map m on m.uid = p.id)),
  ('staff',        (select count(*) from public.auth_user where is_staff),  (select count(*) from public.profiles p join user_map m on m.uid = p.id where p.is_staff)),
  ('contests',     (select count(*) from public.contests_contest),          (select count(*) from public.contests)),
  ('teams',        (select count(*) from public.contests_team),             (select count(*) from public.teams)),
  ('participants', (select count(*) from public.contests_participant),      (select count(*) from public.participants)),
  ('submissions',  (select count(*) from public.contests_submission),       (select count(*) from public.submissions)),
  ('judges',       (select count(*) from public.contests_judge),            (select count(*) from public.judges)),
  ('scores',       (select count(*) from public.contests_score),            (select count(*) from public.scores)),
  ('awards',       (select count(*) from public.contests_award),            (select count(*) from public.awards)),
  ('profiles',     (select count(*) from public.contests_profile),          (select count(*) from public.profiles p join user_map m on m.uid = p.id
                                                                              join public.auth_user u on u.id = m.django_id
                                                                              join public.contests_profile d on d.user_id = u.id)),
  ('reviews',      (select count(*) from public.contests_submissionreview), (select count(*) from public.submission_reviews))
) c(what, django, supabase);

select what, django, supabase, case when django = supabase then 'ok' else '불일치' end as check from counts;

do $$
begin
  if exists (select 1 from counts where django <> supabase) then
    raise exception '건수가 맞지 않아 되돌립니다. 위 표를 확인하세요.';
  end if;
end $$;

\if :{?apply}
commit;
\echo '반영했습니다.'
\else
rollback;
\echo '미리보기라 되돌렸습니다. 반영하려면 -v apply=1 을 붙이세요.'
\endif
