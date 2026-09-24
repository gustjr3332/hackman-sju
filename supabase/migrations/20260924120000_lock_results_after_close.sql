-- 심사가 시작된 뒤에는 대회 결과를 지울 수 없게 한다.
--
-- 지금까지 막혀 있던 것은 제출물·점수의 "등록·수정"뿐이었다. 그래서 (로컬에서 재현)
--   1) 팀원이 종료된 대회의 자기 팀을 지우면 제출물·점수까지 연쇄 삭제되어 순위·시상이 바뀌었다.
--      제출물 보호 트리거가 있지만, 연쇄 삭제 시점에는 팀 행이 이미 지워져 대회를 찾지 못하고
--      통과했다.
--   2) 심사위원은 상태와 관계없이 자기 점수를 지울 수 있었다(채점 트리거는 insert/update 만).
-- 함께 고친 것:
--   3) 점수가 하나라도 있는 대회는 운영자도 지울 수 없었다(위험 구역의 "대회 삭제"). 대회를
--      지우면 심사위원 배정이 연쇄 삭제되는데, 채점한 심사위원 해제를 막는 트리거가 그것까지
--      막았다.
--
-- 대회 자체를 지우는 연쇄 삭제는 계속 허용한다. 그때는 대회 행이 이미 보이지 않으므로
-- ensure_status 가 상태를 찾지 못해 통과한다(ensure_status 의 기존 동작 그대로).

-- ---- 1) 팀 삭제: 제출물과 같은 상태(모집중·진행중)에서만. 운영자도 예외 없음.
create function public.gate_team_delete() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  perform public.ensure_status(old.contest_slug, array['recruiting', 'ongoing'],
    '모집중·진행중 상태에서만 팀을 삭제할 수 있습니다.');
  return old;
end $$;
create trigger teams_gate_delete before delete on public.teams
  for each row execute function public.gate_team_delete();
revoke execute on function public.gate_team_delete() from anon, authenticated, public;

-- ---- 2) 점수 삭제: 채점과 같은 상태(심사중)에서만.
create or replace function public.gate_score() returns trigger
language plpgsql security definer set search_path = '' as $$
declare v_submission bigint := coalesce(new.submission_id, old.submission_id);
begin
  perform public.ensure_status(
    (select t.contest_slug from public.submissions s join public.teams t on t.id = s.team_id
      where s.id = v_submission),
    array['judging'],
    case tg_op when 'DELETE' then '심사중 상태에서만 점수를 지울 수 있습니다.'
      else '심사중 상태에서만 채점할 수 있습니다.' end);
  if tg_op = 'DELETE' then
    return old;
  end if;
  return new;
end $$;
drop trigger scores_gate on public.scores;
create trigger scores_gate before insert or update or delete on public.scores
  for each row execute function public.gate_score();

-- ---- 3) 대회째 지울 때는 채점한 심사위원도 함께 지운다(점수도 같이 지워진다).
create or replace function public.guard_judge_delete() returns trigger
language plpgsql security definer set search_path = '' as $$
declare v_count int;
begin
  if not exists (select 1 from public.contests where slug = old.contest_slug) then
    return old;
  end if;
  select count(*) into v_count from public.scores where judge_id = old.id;
  if v_count > 0 then
    raise exception '이미 채점한 심사위원은 해제할 수 없습니다 (입력한 점수 %건).', v_count using errcode = '42501';
  end if;
  return old;
end $$;
