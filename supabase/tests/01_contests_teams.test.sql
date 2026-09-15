-- 대회·팀·제출물 규칙 (backend tests.py: ContestApi, ContestDelete, ContestStatusTransition, StatusGating 일부)
begin;
create extension if not exists pgtap with schema extensions;
select * from no_plan();

insert into auth.users (id, email, raw_user_meta_data, aud, role) values
  ('00000000-0000-0000-0000-00000000000a', 'org@t.local', '{"username":"org"}', 'authenticated', 'authenticated'),
  ('00000000-0000-0000-0000-00000000000d', 'alice@t.local', '{"username":"alice"}', 'authenticated', 'authenticated'),
  ('00000000-0000-0000-0000-00000000000e', 'bob@t.local', '{"username":"bob"}', 'authenticated', 'authenticated');
update public.profiles set is_staff = true where username = 'org';
insert into public.contests (slug, name, start_at, end_at)
values ('c1', '교내 해커톤', now(), now() + interval '1 day'), ('c2', '다른 대회', now(), now() + interval '1 day');

-- ---- 비로그인
select set_config('request.jwt.claims', '{"role":"anon"}', true);
set local role anon;
select is((select count(*)::int from public.contest_list), 2, '비로그인도 대회 목록을 본다');
select throws_ok($$ insert into public.contests (slug, name, start_at, end_at) values ('x', 'x', now(), now()) $$,
  '42501', null, '비로그인은 대회를 만들 수 없다');
select is((select bool_or(is_judge) from public.contest_list), false, '비로그인의 is_judge 는 항상 false');
reset role;

-- ---- 참가자
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000d","role":"authenticated"}', true);
set local role authenticated;
select throws_ok($$ insert into public.contests (slug, name, start_at, end_at) values ('x', 'x', now(), now()) $$,
  '42501', null, '운영자가 아니면 대회를 만들 수 없다');
select lives_ok($$ insert into public.teams (contest_slug, name) values ('c1', '알파') $$, '모집중에는 팀을 만들 수 있다');
select is((select participants -> 0 ->> 'username' from public.team_list where name = '알파'), 'alice',
  '팀을 만든 사람은 자동으로 팀원이 된다');
select throws_ok($$ insert into public.teams (contest_slug, name) values ('c1', '알파') $$,
  '23505', null, '같은 대회에 같은 이름의 팀은 만들 수 없다');
select lives_ok($$ insert into public.submissions (team_id, title) select id, '제출물' from public.teams where name = '알파' $$,
  '팀원은 제출할 수 있다');
select is((select presentation_due_at from public.team_list where name = '알파'), null,
  '발표를 시작하지 않은 팀은 타이머가 없다');
reset role;

select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000e","role":"authenticated"}', true);
set local role authenticated;
-- RLS 로 막힌 update 는 오류 없이 0행이 된다. 결과로 확인한다.
update public.submissions set title = '가로채기';
reset role;
select is((select title from public.submissions), '제출물', '팀원이 아닌 사람은 남의 제출물을 바꾸지 못한다');

select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000e","role":"authenticated"}', true);
set local role authenticated;
select throws_ok($$ insert into public.submissions (team_id, title) select id, '남의 팀' from public.teams where name = '알파' $$,
  '42501', null, '다른 팀 이름으로 제출할 수 없다');
select throws_ok($$ insert into public.participants (team_id, user_id) select id, '00000000-0000-0000-0000-00000000000d' from public.teams where name = '알파' $$,
  '42501', null, '남을 팀에 넣을 수 없다');
delete from public.contests where slug = 'c1';
reset role;
select is((select count(*)::int from public.contests where slug = 'c1'), 1, '참가자는 대회를 지울 수 없다');

-- ---- 운영자
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000a","role":"authenticated"}', true);
set local role authenticated;
select throws_ok($$ insert into public.contests (slug, name, start_at, end_at) values ('bad', 'x', now(), now() - interval '1 hour') $$,
  '23514', null, '종료가 시작보다 빠르면 거절한다');
select lives_ok($$ update public.contests set status = 'judging' where slug = 'c1' $$, '상태를 건너뛸 수 있다');
select lives_ok($$ update public.contests set status = 'recruiting' where slug = 'c1' $$, '상태를 되돌릴 수 있다');
select throws_ok($$ update public.contests set status = 'done' where slug = 'c1' $$, '23514', null, '없는 상태 값은 거절한다');
select is((select count(*)::int from public.teams where contest_slug = 'c1'), 1, '상태를 되돌려도 팀은 남는다');
reset role;

-- ---- 상태별 허용 동작
update public.contests set status = 'ongoing' where slug = 'c1';
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000e","role":"authenticated"}', true);
set local role authenticated;
select throws_like($$ insert into public.teams (contest_slug, name) values ('c1', '베타') $$,
  '모집중 상태에서만 팀을 만들 수 있습니다. (현재 상태: 진행중)', '진행중에는 팀을 만들 수 없다');
select throws_like($$ insert into public.participants (team_id) select id from public.teams where name = '알파' $$,
  '모집중 상태에서만 팀에 참가할 수 있습니다.%', '진행중에는 팀에 참가할 수 없다');
reset role;

select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000d","role":"authenticated"}', true);
set local role authenticated;
select lives_ok($$ update public.submissions set title = '고친 제출물' $$, '진행중에는 제출물을 고칠 수 있다');
reset role;

update public.contests set status = 'judging' where slug = 'c1';
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000d","role":"authenticated"}', true);
set local role authenticated;
select throws_like($$ update public.submissions set title = '심사 중 수정' $$,
  '심사가 시작된 뒤에는 제출물을 등록하거나 수정할 수 없습니다. (현재 상태: 심사중)', '심사중에는 제출물이 잠긴다');
reset role;

update public.contests set status = 'closed' where slug = 'c1';
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000e","role":"authenticated"}', true);
set local role authenticated;
select throws_like($$ insert into public.participants (team_id) select id from public.teams where name = '알파' $$,
  '%(현재 상태: 종료)', '종료 후에는 팀에 참가할 수 없다');
reset role;

-- ---- 대회 삭제
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000a","role":"authenticated"}', true);
set local role authenticated;
select lives_ok($$ delete from public.contests where slug = 'c1' $$, '운영자는 대회를 지울 수 있다');
reset role;
select is((select count(*)::int from public.teams), 0, '대회를 지우면 팀·제출물까지 함께 지워진다');
select is((select count(*)::int from public.contests where slug = 'c2'), 1, '다른 대회는 남는다');

select * from finish();
rollback;
