-- 프로필·정규 스택·심사 보조 분석 공개 범위 (backend tests.py: ProfileApi, TechStackApi, JudgeAssist 권한)
begin;
create extension if not exists pgtap with schema extensions;
select * from no_plan();

insert into auth.users (id, email, raw_user_meta_data, aud, role) values
  ('00000000-0000-0000-0000-00000000000a', 'org@t.local', '{"username":"org"}', 'authenticated', 'authenticated'),
  ('00000000-0000-0000-0000-00000000000b', 'judge1@t.local', '{"username":"judge1"}', 'authenticated', 'authenticated'),
  ('00000000-0000-0000-0000-00000000000c', 'judge2@t.local', '{"username":"judge2"}', 'authenticated', 'authenticated'),
  ('00000000-0000-0000-0000-00000000000d', 'alice@t.local', '{"username":"alice"}', 'authenticated', 'authenticated'),
  ('00000000-0000-0000-0000-00000000000e', 'bob@t.local', '{"username":"bob"}', 'authenticated', 'authenticated');
update public.profiles set is_staff = true where username = 'org';

select is((select count(*)::int from public.profiles), 5, '가입하면 프로필이 생긴다');
select is((select extraction_status from public.profiles where username = 'alice'), 'empty', '처음 추출 상태는 empty');

-- ---- 프로필
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000d","role":"authenticated"}', true);
set local role authenticated;
select results_eq($$ select username from public.profiles $$, $$ values ('alice'::text) $$, '남의 프로필은 볼 수 없다');
select lives_ok($$ update public.profiles set skills = array['react', 'python'], roles = array['frontend'], level = 'intermediate'
  where id = auth.uid() $$, '추출된 태그를 직접 고칠 수 있다');
select throws_like($$ update public.profiles set skills = array['react', '내가지어낸스택'] where id = auth.uid() $$,
  '목록에 없는 기술 스택입니다: 내가지어낸스택', '목록 밖 스택은 거절한다');
select throws_ok($$ update public.profiles set roles = array['우주비행사'] where id = auth.uid() $$,
  '23514', null, '목록 밖 역할은 거절한다');
select throws_ok($$ update public.profiles set github_url = 'not-a-url' where id = auth.uid() $$,
  '23514', null, '잘못된 GitHub 주소는 거절한다');
select lives_ok($$ update public.profiles set github_url = '' where id = auth.uid() $$, 'GitHub 주소는 비워도 된다');
select throws_ok($$ update public.profiles set other_skills = array['아무거나'] where id = auth.uid() $$,
  '42501', null, 'other_skills 는 직접 쓸 수 없다');
select throws_ok($$ update public.profiles set is_staff = true where id = auth.uid() $$,
  '42501', null, '스스로 운영자가 될 수 없다');
select throws_ok($$ update public.profiles set extraction_status = 'done' where id = auth.uid() $$,
  '42501', null, '추출 상태는 서버만 정한다');
update public.profiles set intro = '새로 쓴 소개' where id = auth.uid();
select is((select extraction_status from public.profiles), 'pending', '자기소개를 바꾸면 추출 결과가 낡은 것으로 표시된다');
select is((select skills from public.profiles), array['react', 'python'], '낡아도 태그는 지우지 않는다');
update public.profiles set intro = '가로채기' where username = 'bob';
reset role;
select is((select intro from public.profiles where username = 'bob'), '', '남의 프로필은 고칠 수 없다');

-- ---- 정규 스택 목록
select set_config('request.jwt.claims', '{"role":"anon"}', true);
set local role anon;
select is((select count(*)::int from public.tech_stacks), 0, '비로그인은 스택 목록을 못 본다');
reset role;
update public.tech_stacks set is_active = false where slug = 'jquery';
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000d","role":"authenticated"}', true);
set local role authenticated;
select is((select count(*)::int from public.tech_stacks), 119, '시드 120건 중 활성 항목만 보인다');
select is((select count(*)::int from public.tech_stacks where slug in ('react', 'python', 'figma')), 3, '시드가 들어 있다');
select throws_ok($$ insert into public.tech_stacks (slug, name) values ('x', 'X') $$, '42501', null, '참가자는 목록을 못 고친다');
select throws_like($$ update public.profiles set skills = array['jquery'] where id = auth.uid() $$,
  '목록에 없는 기술 스택입니다: jquery', '비활성 스택은 새로 고를 수 없다');
reset role;

-- ---- 심사 보조 분석 공개 범위
insert into public.contests (slug, name, start_at, end_at) values
  ('c1', '대회', now(), now() + interval '1 day'), ('c2', '다른 대회', now(), now() + interval '1 day');
insert into public.teams (contest_slug, name) values ('c1', '알파');
insert into public.submissions (team_id, title) select id, '제출물' from public.teams where name = '알파';
insert into public.judges (contest_slug, user_id) values
  ('c1', '00000000-0000-0000-0000-00000000000b'), ('c2', '00000000-0000-0000-0000-00000000000c');
insert into public.submission_reviews (submission_id, provider, model, status)
select id, 'google', 'gemini-3.5-flash', 'done' from public.submissions;

select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000d","role":"authenticated"}', true);
set local role authenticated;
select is((select count(*)::int from public.submission_reviews), 0, '참가자는 분석을 볼 수 없다');
select throws_ok($$ insert into public.submission_reviews (submission_id, provider, model) select id, 'x', 'y' from public.submissions $$,
  '42501', null, '분석은 서버만 쓴다');
reset role;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000b","role":"authenticated"}', true);
set local role authenticated;
select is((select count(*)::int from public.submission_reviews), 1, '배정된 심사위원은 분석을 본다');
reset role;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000c","role":"authenticated"}', true);
set local role authenticated;
select is((select count(*)::int from public.submission_reviews), 0, '다른 대회 심사위원은 못 본다');
reset role;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000a","role":"authenticated"}', true);
set local role authenticated;
select is((select count(*)::int from public.submission_reviews), 1, '운영자는 분석을 본다');
select is((select count(*)::int from public.github_cache), 0, 'GitHub 캐시는 서버 전용이라 조회해도 비어 있다');
reset role;

select * from finish();
rollback;
