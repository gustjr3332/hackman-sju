-- 심사위원·채점·스코어보드 (backend tests.py: MeAndJudgeAssignment, Scoreboard, ScoreboardRanking,
-- ScoreboardPrivacy, StatusGating 채점 부분)
begin;
create extension if not exists pgtap with schema extensions;
select * from no_plan();

insert into auth.users (id, email, raw_user_meta_data, aud, role) values
  ('00000000-0000-0000-0000-00000000000a', 'org@t.local', '{"username":"org"}', 'authenticated', 'authenticated'),
  ('00000000-0000-0000-0000-00000000000b', 'judge1@t.local', '{"username":"judge1"}', 'authenticated', 'authenticated'),
  ('00000000-0000-0000-0000-00000000000c', 'judge2@t.local', '{"username":"judge2"}', 'authenticated', 'authenticated'),
  ('00000000-0000-0000-0000-00000000000d', 'alice@t.local', '{"username":"alice"}', 'authenticated', 'authenticated');
update public.profiles set is_staff = true where username = 'org';
insert into public.contests (slug, name, start_at, end_at, status)
values ('c1', '교내 해커톤', now(), now() + interval '1 day', 'recruiting'),
       ('c2', '다른 대회', now(), now() + interval '1 day', 'judging');
insert into public.teams (contest_slug, name) values ('c1', '알파'), ('c1', '베타'), ('c1', '감마'), ('c1', '델타');
insert into public.submissions (team_id, title) select id, name || ' 제출물' from public.teams where name <> '델타';
update public.contests set status = 'ongoing' where slug = 'c1';

-- ---- 심사위원 배정
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000a","role":"authenticated"}', true);
set local role authenticated;
select is((select username from public.assign_judge('c1', 'judge1')), 'judge1', '운영자는 아이디로 심사위원을 배정한다');
select lives_ok($$ select public.assign_judge('c1', 'judge2') $$, '두 번째 심사위원 배정');
select throws_ok($$ select public.assign_judge('c1', 'nobody') $$, 'P0002', '존재하지 않는 사용자입니다.',
  '없는 아이디는 거절한다');
select throws_ok($$ select public.assign_judge('c1', 'judge1') $$, '23505', '이미 이 대회의 심사위원으로 배정된 사용자입니다.',
  '중복 배정은 한국어 메시지로 거절한다');
reset role;

select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000d","role":"authenticated"}', true);
set local role authenticated;
select throws_ok($$ select public.assign_judge('c1', 'alice') $$, '42501', null, '운영자가 아니면 배정할 수 없다');
select throws_ok($$ update public.judges set contest_slug = 'c2' $$, '42501', null, '배정은 고칠 수 없다(만들고 지우기만)');
reset role;

select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000b","role":"authenticated"}', true);
set local role authenticated;
select is((select is_judge from public.contest_list where slug = 'c1'), true, '배정된 대회는 is_judge = true');
select is((select is_judge from public.contest_list where slug = 'c2'), false, '다른 대회는 is_judge = false');

-- ---- 채점 상태 제한
select throws_like($$ select public.submit_score((select id from public.submissions where title = '알파 제출물'), 'preliminary', 90) $$,
  '심사중 상태에서만 채점할 수 있습니다. (현재 상태: 진행중)', '심사중이 되기 전에는 채점할 수 없다');
reset role;

update public.contests set status = 'judging' where slug = 'c1';

select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000d","role":"authenticated"}', true);
set local role authenticated;
select throws_ok($$ select public.submit_score((select id from public.submissions where title = '알파 제출물'), 'preliminary', 90) $$,
  '42501', '이 대회의 심사위원으로 등록되어 있지 않습니다.', '심사위원이 아니면 채점할 수 없다');
select throws_ok($$ insert into public.scores (submission_id, judge_id, value) values (1, 1, 50) $$,
  '42501', null, '점수 테이블에 직접 쓸 수 없다(submit_score 로만)');
reset role;

-- judge1: 알파 90 베타 80, judge2: 알파 80 베타 90 → 둘 다 평균 85 (공동 1위), 감마 70 → 3위, 델타 미채점
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000b","role":"authenticated"}', true);
set local role authenticated;
select lives_ok($$ select public.submit_score((select id from public.submissions where title = '알파 제출물'), 'preliminary', 50) $$,
  '심사중에는 채점할 수 있다');
select is((select value from public.submit_score((select id from public.submissions where title = '알파 제출물'), 'preliminary', 90, '좋음')),
  90.00::numeric, '같은 라운드에 다시 저장하면 덮어쓴다');
select is((select count(*)::int from public.scores), 1, '덮어써도 행은 하나다');
select lives_ok($$ select public.submit_score((select id from public.submissions where title = '베타 제출물'), 'preliminary', 80) $$, 'judge1 베타');
select lives_ok($$ select public.submit_score((select id from public.submissions where title = '감마 제출물'), 'preliminary', 70) $$, 'judge1 감마');
select lives_ok($$ select public.submit_score((select id from public.submissions where title = '알파 제출물'), 'final', 60) $$, 'judge1 결선');
select throws_ok($$ select public.submit_score((select id from public.submissions where title = '알파 제출물'), 'preliminary', 101) $$,
  '23514', null, '100 을 넘는 점수는 거절한다');
reset role;

select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000c","role":"authenticated"}', true);
set local role authenticated;
select lives_ok($$ select public.submit_score((select id from public.submissions where title = '알파 제출물'), 'preliminary', 80) $$, 'judge2 알파');
select lives_ok($$ select public.submit_score((select id from public.submissions where title = '베타 제출물'), 'preliminary', 90) $$, 'judge2 베타');
select is((select count(*)::int from public.score_list), 2, '심사위원은 자기가 입력한 점수만 본다');
select is((select bool_and(is_mine) from public.score_list), true, 'is_mine 표시');

-- ---- 스코어보드 순위
select results_eq(
  $$ select team_name, average_score, vote_count, rank from public.scoreboard('c1') where round = 'preliminary' $$,
  $$ values ('베타'::text, 85.00::numeric, 2, 1), ('알파', 85.00, 2, 1), ('감마', 70.00, 1, 3), ('델타', null, 0, null) $$,
  '평균 내림차순, 동점은 같은 순위, 다음 순위는 건너뛰고, 미채점 팀은 순위 없이 맨 아래');
select results_eq(
  $$ select team_name, rank from public.scoreboard('c1') where round = 'final' and rank is not null $$,
  $$ values ('알파'::text, 1) $$, '라운드마다 따로 순위를 매긴다');
select is((select count(*)::int from public.scoreboard('c1')), 8, '심사위원은 두 라운드 모두 모든 팀을 본다');
reset role;

-- ---- 결선 비공개
select set_config('request.jwt.claims', '{"role":"anon"}', true);
set local role anon;
select is((select array_agg(distinct round) from public.scoreboard('c1')), array['preliminary'], '비로그인은 예선만 본다');
select is((select count(*)::int from public.score_list), 0, '비로그인은 개별 점수를 못 본다');
reset role;

select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000d","role":"authenticated"}', true);
set local role authenticated;
select is((select array_agg(distinct round) from public.scoreboard('c1')), array['preliminary'], '심사위원이 아닌 로그인 사용자도 예선만');
reset role;

select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000a","role":"authenticated"}', true);
set local role authenticated;
select is((select count(distinct round)::int from public.scoreboard('c1')), 2, '운영자는 두 라운드를 본다');
select is((select count(*)::int from public.score_list), 6, '운영자는 모든 점수를 본다');
select is((select count(*)::int from public.score_list where is_mine), 0, '운영자 본인 점수만 거르는 표시(mine)');

-- ---- 심사위원 해제
select throws_like($$ delete from public.judges where user_id = '00000000-0000-0000-0000-00000000000b' $$,
  '이미 채점한 심사위원은 해제할 수 없습니다 (입력한 점수 4건).', '채점 이력이 있으면 해제할 수 없다');
select is((select score_count from public.judge_list where username = 'judge1'), 4, '심사위원 목록에 점수 수가 나온다');
reset role;

insert into public.judges (contest_slug, user_id) values ('c1', '00000000-0000-0000-0000-00000000000d');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000a","role":"authenticated"}', true);
set local role authenticated;
select lives_ok($$ delete from public.judges where user_id = '00000000-0000-0000-0000-00000000000d' $$, '채점하지 않은 심사위원은 해제된다');
reset role;
select is((select count(*)::int from public.judges where user_id = '00000000-0000-0000-0000-00000000000d'), 0, '해제 확인');

-- ---- 종료 후 수정 금지
update public.contests set status = 'closed' where slug = 'c1';
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000b","role":"authenticated"}', true);
set local role authenticated;
select throws_like($$ select public.submit_score((select id from public.submissions where title = '알파 제출물'), 'preliminary', 10) $$,
  '%(현재 상태: 종료)', '종료 후에는 점수를 바꿀 수 없다');
reset role;

select * from finish();
rollback;
