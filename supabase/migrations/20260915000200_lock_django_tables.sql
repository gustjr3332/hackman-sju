-- Django 가 쓰던 테이블(auth_*, django_*, contests_*)을 API 에서 막는다.
--
-- 운영 DB 에는 Django 테이블이 같은 public 스키마에 들어 있다. Supabase 는 public 에 만든 테이블을
-- anon·authenticated 역할에 기본으로 열어 두고, 이 테이블들은 RLS 가 꺼져 있다. 그래서 anon 키만
-- 있으면 REST 로 auth_user(비밀번호 해시 포함)를 읽고 지울 수 있다. 로컬에서 실제로 확인했다.
-- 지금은 프론트가 anon 키를 쓰지 않아 드러나지 않았지만, 전환하면 키가 번들에 실린다.
-- 전환 전에 반드시 이 마이그레이션이 먼저 들어가야 한다.
--
-- 테이블이 없는 환경(새 프로젝트, 로컬)에서도 그대로 통과한다. 데이터 이전 스크립트는
-- service_role 로 읽으므로 영향이 없다. Django 를 완전히 걷어낸 뒤 테이블은 백업 후 지운다.
do $$
declare t record;
begin
  for t in
    select tablename from pg_tables
    where schemaname = 'public' and tablename ~ '^(auth_|django_|contests_)'
  loop
    execute format('alter table public.%I enable row level security', t.tablename);
    execute format('revoke all on public.%I from anon, authenticated', t.tablename);
  end loop;
end $$;
