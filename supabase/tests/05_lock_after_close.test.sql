-- 심사가 시작된 뒤 결과 삭제 금지 (migrations/20260924120000_lock_results_after_close.sql)
begin;
create extension if not exists pgtap with schema extensions;
select * from no_plan();

insert into auth.users (id, email, raw_user_meta_data, aud, role) values
  ('00000000-0000-0000-0000-00000000000a', 'org@t.local', '{"username":"org"}', 'authenticated', 'authenticated'),
  ('00000000-0000-0000-0000-00000000000b', 'judge1@t.local', '{"username":"judge1"}', 'authenticated', 'authenticated'),
  ('00000000-0000-0000-0000-00000000000d', 'alice@t.local', '{"username":"alice"}', 'authenticated', 'authenticated');
update public.profiles set is_staff = true where username = 'org';
insert into public.contests (slug, name, start_at, end_at)
values ('c1', '교내 해커톤', now(), now() + interval '1 day');

-- alice 가 팀 둘을 만들고 제출한다. 임시 팀은 모집중에 지워 본다.
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000d","role":"authenticated"}', true);
set local role authenticated;
insert into public.teams (contest_slug, name) values ('c1', '알파'), ('c1', '임시');
insert into public.submissions (team_id, title) select id, '알파 제출물' from public.teams where name = '알파';
select lives_ok($$ delete from public.teams where name = '임시' $$, '모집중에는 팀원이 팀을 지울 수 있다');
reset role;
select is((select count(*)::int from public.teams where name = '임시'), 0, '모집중 팀 삭제 확인');

-- 심사중: judge1 이 채점한다.
insert into public.judges (contest_slug, user_id) values ('c1', '00000000-0000-0000-0000-00000000000b');
update public.contests set status = 'judging' where slug = 'c1';
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000b","role":"authenticated"}', true);
set local role authenticated;
select lives_ok($$ select public.submit_score((select id from public.submissions where title = '알파 제출물'), 'preliminary', 80) $$, '채점');
select lives_ok($$ select public.submit_score((select id from public.submissions where title = '알파 제출물'), 'final', 70) $$, '결선 채점');
select lives_ok($$ delete from public.scores where round = 'final' $$, '심사중에는 자기 점수를 지울 수 있다');
reset role;
select is((select count(*)::int from public.scores), 1, '심사중 점수 삭제 확인');

select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000d","role":"authenticated"}', true);
set local role authenticated;
select throws_like($$ delete from public.teams where name = '알파' $$,
  '모집중·진행중 상태에서만 팀을 삭제할 수 있습니다. (현재 상태: 심사중)', '심사중에는 팀을 지울 수 없다');
reset role;

-- 종료: 팀원·심사위원·운영자 모두 결과를 지울 수 없다.
update public.contests set status = 'closed' where slug = 'c1';
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000d","role":"authenticated"}', true);
set local role authenticated;
select throws_like($$ delete from public.teams where name = '알파' $$,
  '%(현재 상태: 종료)', '종료 후에는 팀원이 팀을 지울 수 없다');
reset role;

select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000b","role":"authenticated"}', true);
set local role authenticated;
select throws_like($$ delete from public.scores $$,
  '심사중 상태에서만 점수를 지울 수 있습니다. (현재 상태: 종료)', '종료 후에는 심사위원이 점수를 지울 수 없다');
reset role;

select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000a","role":"authenticated"}', true);
set local role authenticated;
select throws_like($$ delete from public.teams where name = '알파' $$,
  '%(현재 상태: 종료)', '운영자도 종료된 대회의 팀은 지울 수 없다');
select throws_like($$ delete from public.scores $$,
  '%(현재 상태: 종료)', '운영자도 종료된 대회의 점수는 지울 수 없다');
reset role;
select is((select count(*)::int from public.scores), 1, '종료 후 점수는 그대로다');
select results_eq($$ select team_name, rank from public.scoreboard('c1') where round = 'preliminary' $$,
  $$ values ('알파'::text, 1) $$, '종료 후 스코어보드도 그대로다');

-- 대회째 지우는 것은 여전히 된다(채점한 심사위원이 있어도).
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000a","role":"authenticated"}', true);
set local role authenticated;
select lives_ok($$ delete from public.contests where slug = 'c1' $$, '운영자는 점수가 있는 종료 대회도 지울 수 있다');
reset role;
select is((select count(*)::int from public.teams), 0, '팀도 지워진다');
select is((select count(*)::int from public.judges), 0, '심사위원 배정도 지워진다');
select is((select count(*)::int from public.scores), 0, '점수도 지워진다');

select * from finish();
rollback;
