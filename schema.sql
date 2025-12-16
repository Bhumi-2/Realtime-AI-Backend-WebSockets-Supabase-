-- Minimal schema for the assignment (Supabase Postgres)
-- Run this in Supabase SQL Editor.

create table if not exists sessions (
  session_id text primary key,
  user_id text,
  start_time timestamptz not null default now(),
  end_time timestamptz,
  duration_seconds integer,
  summary text
);

create table if not exists session_events (
  id bigserial primary key,
  session_id text not null references sessions(session_id) on delete cascade,
  ts timestamptz not null default now(),
  event_type text not null,            -- e.g. user_message, assistant_message, tool_call, tool_result, system
  role text,                           -- user / assistant / tool / system
  content text not null,
  meta jsonb not null default '{}'::jsonb
);

create index if not exists idx_session_events_session_ts
  on session_events(session_id, ts);
