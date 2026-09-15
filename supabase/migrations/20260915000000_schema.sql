-- HACKMAN 스키마. backend/contests/models.py 와 views.py 의 규칙을 DB 로 옮긴 것이다.
-- 권한은 RLS, 상태별 허용 동작과 한국어 오류 메시지는 트리거, 여러 행을 함께 바꾸는 동작은
-- 함수(RPC)가 맡는다. 화면이 받는 모양은 뷰·함수가 Django 응답과 같게 맞춘다.

create extension if not exists moddatetime schema extensions;

-- ---------------------------------------------------------------- 테이블

create table public.profiles (
  id uuid primary key references auth.users on delete cascade,
  username text not null unique check (username ~ '^[A-Za-z0-9_.@+-]{1,150}$'),
  is_staff boolean not null default false,
  intro text not null default '',
  github_url text not null default '' check (github_url = '' or github_url ~* '^https?://[^\s]+$'),
  skills text[] not null default '{}',
  other_skills text[] not null default '{}',
  interests text[] not null default '{}',
  roles text[] not null default '{}'
    check (roles <@ array['frontend', 'backend', 'mobile', 'design', 'data', 'planning', 'ai']),
  level text not null default '' check (level in ('', 'beginner', 'intermediate', 'advanced')),
  looking_for_team boolean not null default true,
  extraction_status text not null default 'empty'
    check (extraction_status in ('empty', 'pending', 'done', 'failed')),
  extraction_error text not null default '',
  extracted_by text not null default '',
  extracted_at timestamptz,
  updated_at timestamptz not null default now()
);

create table public.contests (
  slug text primary key check (slug ~ '^[-a-zA-Z0-9_]{1,80}$'),
  name text not null check (length(name) between 1 and 200),
  description text not null default '',
  status text not null default 'recruiting' check (status in ('recruiting', 'ongoing', 'judging', 'closed')),
  start_at timestamptz not null,
  end_at timestamptz not null,
  presentation_minutes int not null default 10 check (presentation_minutes between 1 and 30),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint contests_end_after_start check (end_at >= start_at)
);

create table public.teams (
  id bigint generated always as identity primary key,
  contest_slug text not null references public.contests on delete cascade on update cascade,
  name text not null check (length(name) between 1 and 100),
  created_at timestamptz not null default now(),
  presentation_order int check (presentation_order > 0),
  presentation_minutes int check (presentation_minutes between 1 and 30),
  presentation_started_at timestamptz,
  presentation_ended_at timestamptz,
  constraint teams_contest_name_key unique (contest_slug, name)
);

create table public.participants (
  id bigint generated always as identity primary key,
  team_id bigint not null references public.teams on delete cascade,
  user_id uuid not null references public.profiles on delete cascade default auth.uid(),
  joined_at timestamptz not null default now(),
  constraint participants_team_user_key unique (team_id, user_id)
);

create table public.submissions (
  id bigint generated always as identity primary key,
  team_id bigint not null unique references public.teams on delete cascade,
  title text not null check (length(title) between 1 and 200),
  description text not null default '',
  link_url text not null default '' check (link_url = '' or link_url ~* '^https?://[^\s]+$'),
  repo_url text not null default '' check (repo_url = '' or repo_url ~* '^https?://[^\s]+$'),
  submitted_at timestamptz not null default now()
);

create table public.judges (
  id bigint generated always as identity primary key,
  contest_slug text not null references public.contests on delete cascade on update cascade,
  user_id uuid not null references public.profiles on delete cascade,
  constraint judges_contest_user_key unique (contest_slug, user_id)
);

create table public.scores (
  id bigint generated always as identity primary key,
  submission_id bigint not null references public.submissions on delete cascade,
  judge_id bigint not null references public.judges on delete cascade,
  round text not null default 'preliminary' check (round in ('preliminary', 'final')),
  value numeric(5, 2) not null check (value between 0 and 100),
  comment text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint scores_submission_judge_round_key unique (submission_id, judge_id, round)
);

create table public.awards (
  id bigint generated always as identity primary key,
  contest_slug text not null references public.contests on delete cascade on update cascade,
  rank int not null check (rank > 0),
  title text not null check (length(title) between 1 and 50),
  constraint awards_contest_rank_key unique (contest_slug, rank)
);

create table public.tech_stacks (
  slug text primary key check (slug ~ '^[-a-z0-9]{1,60}$'),
  name text not null,
  category text not null default 'language' check (category in ('language', 'framework', 'tool')),
  aliases text[] not null default '{}',
  is_active boolean not null default true
);

create table public.submission_reviews (
  id bigint generated always as identity primary key,
  submission_id bigint not null references public.submissions on delete cascade,
  provider text not null,
  model text not null,
  status text not null default 'pending' check (status in ('pending', 'done', 'failed')),
  summary text not null default '',
  findings jsonb not null default '[]',
  cited_paths text[] not null default '{}',
  stack text[] not null default '{}',
  truncated boolean not null default false,
  files_read int not null default 0,
  input_tokens int not null default 0,
  output_tokens int not null default 0,
  error text not null default '',
  submission_seen_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint submission_reviews_key unique (submission_id, provider, model)
);

-- Edge Function 이 GitHub 응답을 담아 두는 곳(LocMemCache 대신). 서비스 키만 쓴다.
create table public.github_cache (
  key text primary key,
  payload jsonb not null,
  link text not null default '',
  expires_at timestamptz not null
);

create index on public.teams (contest_slug);
create index on public.participants (user_id);
create index on public.judges (user_id);
create index on public.scores (judge_id);
create index on public.awards (contest_slug);

create trigger contests_updated_at before update on public.contests
  for each row execute procedure extensions.moddatetime(updated_at);
create trigger scores_updated_at before update on public.scores
  for each row execute procedure extensions.moddatetime(updated_at);
create trigger submissions_submitted_at before update on public.submissions
  for each row execute procedure extensions.moddatetime(submitted_at);
create trigger profiles_updated_at before update on public.profiles
  for each row execute procedure extensions.moddatetime(updated_at);
create trigger submission_reviews_updated_at before update on public.submission_reviews
  for each row execute procedure extensions.moddatetime(updated_at);

-- ---------------------------------------------------------------- 판별 함수
-- RLS 안에서 쓰므로 SECURITY DEFINER 로 RLS 를 돌아 조회한다(재귀 방지).

create function public.is_organizer() returns boolean
language sql stable security definer set search_path = '' as $$
  select coalesce((select is_staff from public.profiles where id = auth.uid()), false)
$$;

create function public.is_judge_of(p_slug text) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (select 1 from public.judges where contest_slug = p_slug and user_id = auth.uid())
$$;

create function public.is_member_of(p_team_id bigint) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (select 1 from public.participants where team_id = p_team_id and user_id = auth.uid())
$$;

-- 아이디는 팀원·심사위원 표시에 쓰이는 공개 정보다. 프로필의 나머지는 본인·운영자만 본다.
create function public.username_of(p_user_id uuid) returns text
language sql stable security definer set search_path = '' as $$
  select username from public.profiles where id = p_user_id
$$;

create function public.status_label(p_status text) returns text
language sql immutable set search_path = '' as $$
  select case p_status when 'recruiting' then '모집중' when 'ongoing' then '진행중'
    when 'judging' then '심사중' when 'closed' then '종료' end
$$;

-- ---------------------------------------------------------------- 상태별 허용 동작
-- views.py 의 *_STATUSES 와 같다(frontend/src/rules.ts 도 같은 규칙). 운영자도 예외가 없다.

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

-- 팀을 만든 사람은 그 팀에 자동으로 들어간다.
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

create function public.gate_participant_insert() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  perform public.ensure_status((select contest_slug from public.teams where id = new.team_id),
    array['recruiting'], '모집중 상태에서만 팀에 참가할 수 있습니다.');
  return new;
end $$;
create trigger participants_gate before insert on public.participants
  for each row execute function public.gate_participant_insert();

create function public.gate_submission() returns trigger
language plpgsql security definer set search_path = '' as $$
declare v_team bigint := coalesce(new.team_id, old.team_id);
begin
  perform public.ensure_status((select contest_slug from public.teams where id = v_team),
    array['recruiting', 'ongoing'], '심사가 시작된 뒤에는 제출물을 등록하거나 수정할 수 없습니다.');
  if tg_op = 'DELETE' then
    return old;
  end if;
  return new;
end $$;
create trigger submissions_gate before insert or update or delete on public.submissions
  for each row execute function public.gate_submission();

create function public.gate_score() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  perform public.ensure_status(
    (select t.contest_slug from public.submissions s join public.teams t on t.id = s.team_id
      where s.id = new.submission_id),
    array['judging'], '심사중 상태에서만 채점할 수 있습니다.');
  return new;
end $$;
create trigger scores_gate before insert or update on public.scores
  for each row execute function public.gate_score();

-- 심사위원을 빼면 점수가 CASCADE 로 지워져 순위가 바뀐다. 채점 이력이 있으면 막는다.
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

-- ---------------------------------------------------------------- 프로필

-- 가입하면 프로필이 생긴다. 아이디는 가입할 때 넘긴 user_metadata.username.
create function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  insert into public.profiles (id, username)
  values (new.id, coalesce(new.raw_user_meta_data ->> 'username', split_part(new.email, '@', 1)));
  return new;
end $$;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();

create function public.check_profile() returns trigger
language plpgsql security definer set search_path = '' as $$
declare v_unknown text;
begin
  -- 자기소개 원문이 바뀌면 기존 추출 결과는 그 원문에서 나온 것이 아니다. 지우지는 않는다.
  if tg_op = 'UPDATE' and new.intro is distinct from old.intro then
    new.extraction_status := 'pending';
  end if;
  -- 기술 스택은 정규 목록의 활성 항목만 받는다. 목록 밖 값이 섞이면 매칭이 조용히 망가진다.
  select string_agg(s, ', ') into v_unknown
  from unnest(new.skills) s
  where not exists (select 1 from public.tech_stacks t where t.slug = s and t.is_active);
  if v_unknown is not null then
    raise exception '목록에 없는 기술 스택입니다: %', v_unknown using errcode = '23514';
  end if;
  return new;
end $$;
create trigger profiles_check before insert or update on public.profiles
  for each row execute function public.check_profile();

-- ---------------------------------------------------------------- 실시간
-- 공개 채널로 "바뀌었다"는 신호만 보낸다. 점수를 싣지 않으므로 결선 비공개가 새지 않고,
-- 화면은 신호를 받으면 권한에 맞게 걸러 주는 뷰·함수를 다시 부른다.

create function public.broadcast_contest_change() returns trigger
language plpgsql security definer set search_path = '' as $$
declare
  -- 테이블마다 열이 달라 레코드 필드를 직접 읽으면 없는 열에서 오류가 난다. jsonb 로 읽는다.
  j jsonb := to_jsonb(coalesce(new, old));
  v_slug text;
begin
  v_slug := case tg_table_name
    when 'contests' then j ->> 'slug'
    when 'teams' then j ->> 'contest_slug'
    when 'participants' then (select contest_slug from public.teams where id = (j ->> 'team_id')::bigint)
    when 'submissions' then (select contest_slug from public.teams where id = (j ->> 'team_id')::bigint)
    when 'scores' then (select t.contest_slug from public.submissions s join public.teams t on t.id = s.team_id
                          where s.id = (j ->> 'submission_id')::bigint)
  end;
  if v_slug is not null then
    perform realtime.send(jsonb_build_object('table', tg_table_name), 'changed', 'contest:' || v_slug, false);
  end if;
  return null;
end $$;

create trigger contests_broadcast after insert or update or delete on public.contests
  for each row execute function public.broadcast_contest_change();
create trigger teams_broadcast after insert or update or delete on public.teams
  for each row execute function public.broadcast_contest_change();
create trigger participants_broadcast after insert or update or delete on public.participants
  for each row execute function public.broadcast_contest_change();
create trigger submissions_broadcast after insert or update or delete on public.submissions
  for each row execute function public.broadcast_contest_change();
create trigger scores_broadcast after insert or update or delete on public.scores
  for each row execute function public.broadcast_contest_change();

-- ---------------------------------------------------------------- RLS

alter table public.profiles enable row level security;
alter table public.contests enable row level security;
alter table public.teams enable row level security;
alter table public.participants enable row level security;
alter table public.submissions enable row level security;
alter table public.judges enable row level security;
alter table public.scores enable row level security;
alter table public.awards enable row level security;
alter table public.tech_stacks enable row level security;
alter table public.submission_reviews enable row level security;
alter table public.github_cache enable row level security;

create policy "본인·운영자만 조회" on public.profiles for select
  using (id = auth.uid() or public.is_organizer());
create policy "본인만 수정" on public.profiles for update
  using (id = auth.uid()) with check (id = auth.uid());

create policy "누구나 조회" on public.contests for select to anon, authenticated using (true);
create policy "운영자만 생성" on public.contests for insert with check (public.is_organizer());
create policy "운영자만 수정" on public.contests for update using (public.is_organizer());
create policy "운영자만 삭제" on public.contests for delete using (public.is_organizer());

create policy "누구나 조회" on public.teams for select to anon, authenticated using (true);
create policy "로그인하면 생성" on public.teams for insert to authenticated with check (true);
create policy "팀원·운영자 수정" on public.teams for update
  using (public.is_member_of(id) or public.is_organizer());
create policy "팀원·운영자 삭제" on public.teams for delete
  using (public.is_member_of(id) or public.is_organizer());

create policy "누구나 조회" on public.participants for select to anon, authenticated using (true);
create policy "본인만 참가" on public.participants for insert to authenticated
  with check (user_id = auth.uid());
create policy "운영자만 삭제" on public.participants for delete using (public.is_organizer());

create policy "누구나 조회" on public.submissions for select to anon, authenticated using (true);
create policy "팀원·운영자 등록" on public.submissions for insert
  with check (public.is_member_of(team_id) or public.is_organizer());
create policy "팀원·운영자 수정" on public.submissions for update
  using (public.is_member_of(team_id) or public.is_organizer());
create policy "팀원·운영자 삭제" on public.submissions for delete
  using (public.is_member_of(team_id) or public.is_organizer());

create policy "누구나 조회" on public.judges for select to anon, authenticated using (true);
create policy "운영자만 해제" on public.judges for delete using (public.is_organizer());

-- 점수는 운영자는 전부, 그 외에는 자기가 입력한 것만 본다. 입력·수정은 submit_score 로만 한다.
create policy "본인 점수·운영자 조회" on public.scores for select
  using (public.is_organizer()
    or exists (select 1 from public.judges j where j.id = judge_id and j.user_id = auth.uid()));
create policy "본인 점수·운영자 삭제" on public.scores for delete
  using (public.is_organizer()
    or exists (select 1 from public.judges j where j.id = judge_id and j.user_id = auth.uid()));

-- 시상 정보는 공개 전까지 읽기까지 운영자 전용이다.
create policy "운영자 전용" on public.awards for all using (public.is_organizer())
  with check (public.is_organizer());

create policy "로그인하면 활성 목록 조회" on public.tech_stacks for select to authenticated
  using (is_active or public.is_organizer());

-- 심사 보조 분석은 운영자·그 대회 심사위원만. 쓰기는 Edge Function(서비스 키)만 한다.
create policy "운영자·배정 심사위원 조회" on public.submission_reviews for select
  using (public.is_organizer() or exists (
    select 1 from public.submissions s join public.teams t on t.id = s.team_id
    where s.id = submission_id and public.is_judge_of(t.contest_slug)));

-- 열 단위로 막아야 하는 것: 권한 필드와 서버가 정하는 필드는 사용자가 못 바꾼다.
revoke insert, update on public.profiles from anon, authenticated;
grant update (intro, github_url, skills, interests, roles, level, looking_for_team)
  on public.profiles to authenticated;
revoke update on public.teams from anon, authenticated;
grant update (name) on public.teams to authenticated;
revoke update on public.submissions from anon, authenticated;
grant update (title, description, link_url, repo_url) on public.submissions to authenticated;
revoke insert, update on public.scores from anon, authenticated;
-- 배정은 만들고 지우기만 한다. 고치면 그 심사위원의 점수가 다른 사람·다른 대회 것이 된다.
revoke insert, update on public.judges from anon, authenticated;

-- ---------------------------------------------------------------- 조회용 뷰 (Django 응답 모양)

create view public.contest_list with (security_invoker = true) as
select c.slug, c.name, c.description, c.status, c.start_at, c.end_at, c.created_at, c.updated_at,
  (select count(*) from public.teams t where t.contest_slug = c.slug)::int as team_count,
  public.is_judge_of(c.slug) as is_judge,
  c.presentation_minutes
from public.contests c;

create view public.team_list with (security_invoker = true) as
select t.id, t.contest_slug as contest, t.name, t.created_at,
  coalesce((select json_agg(json_build_object('id', p.id, 'team', p.team_id,
      'username', public.username_of(p.user_id), 'joined_at', p.joined_at) order by p.joined_at)
    from public.participants p where p.team_id = t.id), '[]') as participants,
  (select json_build_object('id', s.id, 'team', s.team_id, 'title', s.title, 'description', s.description,
      'link_url', s.link_url, 'repo_url', s.repo_url, 'submitted_at', s.submitted_at)
    from public.submissions s where s.team_id = t.id) as submission,
  t.presentation_order, t.presentation_minutes,
  coalesce(t.presentation_minutes, c.presentation_minutes) as effective_presentation_minutes,
  t.presentation_started_at, t.presentation_ended_at,
  case when t.presentation_started_at is not null and t.presentation_ended_at is null
    then t.presentation_started_at + make_interval(mins => coalesce(t.presentation_minutes, c.presentation_minutes))
  end as presentation_due_at
from public.teams t join public.contests c on c.slug = t.contest_slug;

create function public.judge_score_count(p_judge_id bigint) returns int
language sql stable security definer set search_path = '' as $$
  select count(*)::int from public.scores where judge_id = p_judge_id
$$;

create view public.judge_list with (security_invoker = true) as
select j.id, j.contest_slug as contest, public.username_of(j.user_id) as username,
  public.judge_score_count(j.id) as score_count
from public.judges j;

create view public.score_list with (security_invoker = true) as
select sc.id, sc.submission_id as submission, sc.judge_id as judge,
  public.username_of(j.user_id) as judge_username, sc.round, sc.value, sc.comment,
  sc.created_at, sc.updated_at, t.contest_slug as contest, (j.user_id = auth.uid()) as is_mine
from public.scores sc
join public.judges j on j.id = sc.judge_id
join public.submissions s on s.id = sc.submission_id
join public.teams t on t.id = s.team_id;

-- ---------------------------------------------------------------- RPC

-- 스코어보드: 라운드별 평균·심사 수·순위. 동점은 같은 순위, 다음 순위는 건너뛴다(1, 1, 3).
-- 점수 없는 팀은 순위 없이 맨 아래. 결선은 운영자·그 대회 심사위원에게만 준다.
create function public.scoreboard(p_slug text)
returns table (team_id bigint, team_name text, submission_title text, round text,
               average_score numeric, vote_count int, rank int)
language sql stable security definer set search_path = '' as $$
  with rounds as (
    select r, o from unnest(array['preliminary', 'final']) with ordinality as u (r, o)
    where r = 'preliminary' or public.is_organizer() or public.is_judge_of(p_slug)
  ), agg as (
    select t.id as team_id, t.name as team_name, s.title as submission_title, rd.r as round, rd.o,
      avg(sc.value) as avg_raw, count(sc.id)::int as vote_count
    from public.teams t
    cross join rounds rd
    left join public.submissions s on s.team_id = t.id
    left join public.scores sc on sc.submission_id = s.id and sc.round = rd.r
    where t.contest_slug = p_slug
    group by t.id, t.name, s.title, rd.r, rd.o
  )
  select team_id, team_name, submission_title, round, round(avg_raw, 2), vote_count,
    case when avg_raw is not null
      then (rank() over (partition by round, avg_raw is null order by avg_raw desc))::int end
  from agg
  -- 동점·미채점 팀은 이름 코드포인트순(Django 의 파이썬 정렬과 같게 C 콜레이션).
  order by o, avg_raw desc nulls last, team_name collate "C"
$$;

-- 채점: 같은 심사위원이 같은 라운드에 다시 저장하면 덮어쓴다(upsert). 심사위원은 요청자
-- 본인으로 서버가 정한다(클라이언트가 보낸 judge 는 받지 않는다).
create function public.submit_score(p_submission_id bigint, p_round text, p_value numeric,
                                    p_comment text default '')
returns setof public.score_list
language plpgsql security definer set search_path = '' as $$
declare v_slug text; v_judge bigint; v_id bigint;
begin
  select t.contest_slug into v_slug
  from public.submissions s join public.teams t on t.id = s.team_id where s.id = p_submission_id;
  if v_slug is null then
    raise exception '제출물을 찾을 수 없습니다.' using errcode = 'P0002';
  end if;
  select id into v_judge from public.judges where contest_slug = v_slug and user_id = auth.uid();
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

create function public.assign_judge(p_slug text, p_username text)
returns setof public.judge_list
language plpgsql security definer set search_path = '' as $$
declare v_user uuid; v_id bigint;
begin
  if not public.is_organizer() then
    raise exception '운영자만 심사위원을 배정할 수 있습니다.' using errcode = '42501';
  end if;
  select id into v_user from public.profiles where username = p_username;
  if v_user is null then
    raise exception '존재하지 않는 사용자입니다.' using errcode = 'P0002';
  end if;
  insert into public.judges (contest_slug, user_id) values (p_slug, v_user) returning id into v_id;
  return query select * from public.judge_list where id = v_id;
exception when unique_violation then
  raise exception '이미 이 대회의 심사위원으로 배정된 사용자입니다.' using errcode = '23505';
end $$;

create function public.require_organizer() returns void
language plpgsql stable security definer set search_path = '' as $$
begin
  if not public.is_organizer() then
    raise exception '운영자만 할 수 있습니다.' using errcode = '42501';
  end if;
end $$;

-- 발표 순서·발표 시간은 운영자만 정한다(팀원은 팀 이름만 바꿀 수 있다).
create function public.set_team_schedule(p_team_id bigint, p_order int, p_minutes int)
returns setof public.team_list
language plpgsql security definer set search_path = '' as $$
begin
  perform public.require_organizer();
  update public.teams set presentation_order = p_order, presentation_minutes = p_minutes where id = p_team_id;
  return query select * from public.team_list where id = p_team_id;
end $$;

-- 제출 시각순으로 발표 순서를 한 번에 매긴다. 제출하지 않은 팀은 뒤로, 이름순.
create function public.assign_presentation_order(p_slug text)
returns setof public.team_list
language plpgsql security definer set search_path = '' as $$
begin
  perform public.require_organizer();
  update public.teams t set presentation_order = o.n
  from (
    select t2.id, row_number() over (order by s.id is null, s.submitted_at, t2.name) as n
    from public.teams t2 left join public.submissions s on s.team_id = t2.id
    where t2.contest_slug = p_slug
  ) o where t.id = o.id;
  return query select * from public.team_list where contest = p_slug order by presentation_order;
end $$;

-- 발표 시작: 실제 누른 시각을 기록한다. 한 대회에서 두 팀이 동시에 발표할 수 없으므로
-- 아직 끝나지 않은 다른 팀은 같은 시각으로 끝낸다.
create function public.start_presentation(p_team_id bigint)
returns setof public.team_list
language plpgsql security definer set search_path = '' as $$
declare v_slug text; v_now timestamptz := now();
begin
  perform public.require_organizer();
  select contest_slug into v_slug from public.teams where id = p_team_id;
  update public.teams set presentation_ended_at = v_now
  where contest_slug = v_slug and id <> p_team_id
    and presentation_started_at is not null and presentation_ended_at is null;
  update public.teams set presentation_started_at = v_now, presentation_ended_at = null where id = p_team_id;
  return query select * from public.team_list where id = p_team_id;
end $$;

create function public.end_presentation(p_team_id bigint)
returns setof public.team_list
language plpgsql security definer set search_path = '' as $$
begin
  perform public.require_organizer();
  if (select presentation_started_at from public.teams where id = p_team_id) is null then
    raise exception '아직 시작하지 않은 발표입니다.' using errcode = '22023';
  end if;
  update public.teams set presentation_ended_at = now() where id = p_team_id;
  return query select * from public.team_list where id = p_team_id;
end $$;

create function public.reset_presentation(p_team_id bigint)
returns setof public.team_list
language plpgsql security definer set search_path = '' as $$
begin
  perform public.require_organizer();
  update public.teams set presentation_started_at = null, presentation_ended_at = null where id = p_team_id;
  return query select * from public.team_list where id = p_team_id;
end $$;

-- 내부용 함수는 API 로 직접 부르지 못하게 한다(트리거·다른 함수 안에서만 쓴다).
revoke execute on function public.ensure_status(text, text[], text) from anon, authenticated, public;
revoke execute on function public.gate_team_insert() from anon, authenticated, public;
revoke execute on function public.join_creator() from anon, authenticated, public;
revoke execute on function public.gate_participant_insert() from anon, authenticated, public;
revoke execute on function public.gate_submission() from anon, authenticated, public;
revoke execute on function public.gate_score() from anon, authenticated, public;
revoke execute on function public.guard_judge_delete() from anon, authenticated, public;
revoke execute on function public.handle_new_user() from anon, authenticated, public;
revoke execute on function public.check_profile() from anon, authenticated, public;
revoke execute on function public.broadcast_contest_change() from anon, authenticated, public;
