-- Data API(PostgREST) 권한을 명시한다.
--
-- 2026-10-30부터 Supabase 는 public 에 새로 만드는 테이블·뷰에 anon·authenticated·service_role
-- 권한을 자동으로 주지 않는다(새 프로젝트는 2026-05-30부터). 운영 DB 의 기존 테이블은 권한을 그대로
-- 갖고 있어 영향이 없지만, 이 저장소의 마이그레이션으로 DB 를 새로 만들면(로컬 db reset, 새 프로젝트,
-- 프리뷰 브랜치) 테이블·뷰에 API 로 닿지 못한다.
--
-- 그래서 지금까지 자동으로 붙던 권한에서 20260915000000_schema.sql 이 일부러 거둬들인 것을 뺀
-- 나머지를 그대로 적는다. 운영·기존 로컬에서는 이미 있는 권한이라 아무것도 바뀌지 않는다.
-- 실제 접근 통제는 RLS 와 트리거가 한다(이 권한은 "API 가 이 테이블을 볼 수 있는가"까지만).
-- 열 단위 권한(profiles·teams·submissions 의 update)은 schema.sql 에 이미 명시돼 있다.
-- identity 열의 시퀀스 권한은 insert 에 필요 없다(로컬에서 확인).
--
-- 앞으로 public 에 테이블·뷰를 새로 만들면 같은 마이그레이션 안에 grant 를 직접 적는다.

grant select, insert, update, delete on
  public.contests, public.participants, public.awards, public.tech_stacks,
  public.submission_reviews, public.github_cache,
  public.contest_list, public.team_list, public.judge_list, public.score_list
  to anon, authenticated, service_role;

-- schema.sql 이 insert·update 를 거둬들인 테이블은 남은 권한만.
grant select, delete on public.profiles, public.judges, public.scores to anon, authenticated;
grant select, insert, delete on public.teams, public.submissions to anon, authenticated;
grant select, insert, update, delete on
  public.profiles, public.judges, public.scores, public.teams, public.submissions
  to service_role;
