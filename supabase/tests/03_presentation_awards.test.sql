-- 발표 일정·시상 (backend tests.py: PresentationSchedule, AwardApi)
begin;
create extension if not exists pgtap with schema extensions;
select * from no_plan();

insert into auth.users (id, email, raw_user_meta_data, aud, role) values
  ('00000000-0000-0000-0000-00000000000a', 'org@t.local', '{"username":"org"}', 'authenticated', 'authenticated'),
  ('00000000-0000-0000-0000-00000000000d', 'alice@t.local', '{"username":"alice"}', 'authenticated', 'authenticated');
update public.profiles set is_staff = true where username = 'org';
insert into public.contests (slug, name, start_at, end_at, presentation_minutes)
values ('c1', '교내 해커톤', now(), now() + interval '1 day', 7);
insert into public.teams (contest_slug, name) values ('c1', '가'), ('c1', '나'), ('c1', '다');
-- 제출 시각: 나 → 가, 다는 미제출
insert into public.submissions (team_id, title, submitted_at) select id, '나', now() - interval '2 hour' from public.teams where name = '나';
insert into public.submissions (team_id, title, submitted_at) select id, '가', now() - interval '1 hour' from public.teams where name = '가';
insert into public.participants (team_id, user_id) select id, '00000000-0000-0000-0000-00000000000d' from public.teams where name = '가';

-- ---- 참가자는 일정을 못 바꾼다
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000d","role":"authenticated"}', true);
set local role authenticated;
select throws_ok($$ select public.assign_presentation_order('c1') $$, '42501', null, '운영자가 아니면 발표 순서를 배정할 수 없다');
select throws_ok($$ select public.set_team_schedule((select id from public.teams where name = '가'), 1, 5) $$,
  '42501', null, '팀원도 자기 팀 발표 순서를 바꿀 수 없다');
select throws_ok($$ update public.teams set presentation_order = 1 where name = '가' $$, '42501', null,
  '발표 순서 열은 직접 쓸 수 없다');
select lives_ok($$ update public.teams set name = '가나' where name = '가' $$, '팀원은 팀 이름을 바꿀 수 있다');
select throws_ok($$ select public.start_presentation((select id from public.teams where name = '가나')) $$,
  '42501', null, '참가자는 발표를 시작할 수 없다');
reset role;

-- ---- 운영자
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000a","role":"authenticated"}', true);
set local role authenticated;
select results_eq($$ select name, presentation_order from public.assign_presentation_order('c1') $$,
  $$ values ('나'::text, 1), ('가나', 2), ('다', 3) $$, '제출 시각순, 미제출 팀은 뒤로');
select is((select presentation_order from public.set_team_schedule((select id from public.teams where name = '다'), 1, 12)),
  1, '운영자는 순서를 자유롭게 바꾼다');
select is((select effective_presentation_minutes from public.team_list where name = '다'), 12, '팀별 발표 시간');
select is((select effective_presentation_minutes from public.team_list where name = '나'), 7, '팀 시간이 없으면 대회 기본값');
select throws_ok($$ select public.set_team_schedule((select id from public.teams where name = '다'), 1, 31) $$,
  '23514', null, '30분을 넘는 발표 시간은 거절한다');

select throws_ok($$ select public.end_presentation((select id from public.teams where name = '나')) $$,
  '22023', '아직 시작하지 않은 발표입니다.', '시작하지 않은 발표는 끝낼 수 없다');
select isnt((select presentation_due_at from public.start_presentation((select id from public.teams where name = '나'))),
  null, '발표를 시작하면 종료 예정 시각이 생긴다');
select is((select presentation_due_at - presentation_started_at from public.team_list where name = '나'),
  interval '7 minutes', '종료 예정 = 시작 + 발표 시간');
select lives_ok($$ select public.start_presentation((select id from public.teams where name = '다')) $$, '다음 팀 시작');
select isnt((select presentation_ended_at from public.team_list where name = '나'), null,
  '다른 팀을 시작하면 진행 중이던 팀은 끝난다');
select is((select count(*)::int from public.team_list where presentation_due_at is not null), 1,
  '한 대회에서 타이머는 하나만 돈다');
select is((select presentation_due_at from public.end_presentation((select id from public.teams where name = '다'))),
  null, '발표를 끝내면 타이머가 멈춘다');
select is((select presentation_started_at from public.reset_presentation((select id from public.teams where name = '다'))),
  null, '잘못 누른 발표는 되돌릴 수 있다');

-- ---- 시상
select lives_ok($$ insert into public.awards (contest_slug, rank, title) values ('c1', 1, '대상') $$, '운영자는 상을 등록한다');
select throws_ok($$ insert into public.awards (contest_slug, rank, title) values ('c1', 1, '또 대상') $$,
  '23505', null, '같은 등수에 상을 두 번 등록할 수 없다');
select lives_ok($$ update public.awards set title = '최우수상' where rank = 1 $$, '상 이름을 고칠 수 있다');
reset role;

select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000d","role":"authenticated"}', true);
set local role authenticated;
select is((select count(*)::int from public.awards), 0, '운영자가 아니면 시상 정보를 못 본다');
reset role;
select set_config('request.jwt.claims', '{"role":"anon"}', true);
set local role anon;
select is((select count(*)::int from public.awards), 0, '비로그인도 시상 정보를 못 본다');
reset role;

select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000a","role":"authenticated"}', true);
set local role authenticated;
select lives_ok($$ delete from public.awards where rank = 1 $$, '운영자는 상을 지운다');
reset role;
select is((select count(*)::int from public.awards), 0, '삭제 확인');

select * from finish();
rollback;
