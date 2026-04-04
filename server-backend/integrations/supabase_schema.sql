-- 1. Ensure extension
create extension if not exists vector;

-- 2. Update inetartction table (SAFE ALTERS)
alter table public.inetartction
  add column if not exists metadata jsonb default null;

alter table public.inetartction
  add column if not exists importance float default 0.5;

-- Drop embedding index first so column replacement cannot fail due dependency
drop index if exists inetartction_embedding_idx;

-- Replace embedding only when it is specifically vector(1536)
do $$
begin
  if exists (
    select 1
    from pg_attribute a
    join pg_class c on c.oid = a.attrelid
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public'
      and c.relname = 'inetartction'
      and a.attname = 'embedding'
      and a.attisdropped = false
      and format_type(a.atttypid, a.atttypmod) = 'vector(1536)'
  ) then
    alter table public.inetartction drop column embedding;
  end if;
end $$;

-- Recreate embedding with 768 only if missing
alter table public.inetartction
  add column if not exists embedding vector(768);

-- 3. Create user_profile table
create table if not exists public.user_profile (
  user_id text primary key,
  preferences jsonb default '{}',
  facts jsonb default '{}',
  communication_style text default 'balanced',
  updated_at timestamptz default now()
);

-- 4. Indexes (SAFE)
create index if not exists inetartction_user_id_idx
  on public.inetartction (user_id);

create index if not exists inetartction_created_at_idx
  on public.inetartction (created_at desc);

create index if not exists inetartction_metadata_idx
  on public.inetartction using gin (metadata);

create index if not exists inetartction_embedding_idx
  on public.inetartction
  using ivfflat (embedding vector_cosine_ops)
  with (lists = 100);

create index if not exists user_profile_user_id_idx
  on public.user_profile (user_id);

-- 5. Drop all old versions of function
do $$
declare
  signature text;
begin
  for signature in
    select pg_get_function_identity_arguments(p.oid)
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public'
      and p.proname = 'match_memory'
  loop
    execute format('drop function if exists public.match_memory(%s);', signature);
  end loop;
end $$;

-- 6. Create new function (improved ranking + unambiguous parameter use)
create function public.match_memory(
  query_embedding vector(768),
  match_count int,
  user_id text
)
returns table (
  id bigint,
  user_text text,
  aries_text text,
  created_at timestamptz,
  similarity float,
  recency_score float,
  importance float,
  score float
)
language sql
stable
as $$
  select
    i.id,
    i.user_text,
    i.aries_text,
    i.created_at,
    1 - (i.embedding <=> query_embedding) as similarity,
    (1 / (extract(epoch from now() - i.created_at) + 1))::float as recency_score,
    coalesce(i.importance, 0.5)::float as importance,
    (
      (1 - (i.embedding <=> query_embedding)) * 0.6 +
      coalesce(i.importance, 0.5) * 0.2 +
      ((1 / (extract(epoch from now() - i.created_at) + 1)) * 0.2)
    )::float as score
  from public.inetartction as i
  where i.user_id = match_memory.user_id
    and i.embedding is not null
  order by score desc
  limit match_memory.match_count;
$$;

-- 7. Refresh schema cache
notify pgrst, 'reload schema';

-- 8. Analyze for performance
analyze public.inetartction;
