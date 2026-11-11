-- Shared chat names and per-session room membership.
-- Only the Edge Function's service role may access these tables.

create table if not exists public.chat_api_rooms (
  room_code text primary key check (room_code ~ '^[0-9]{6}$'),
  room_name text not null check (
    char_length(trim(room_name)) between 1 and 40
  ),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.chat_api_room_members (
  room_code text not null references public.chat_api_rooms(room_code) on delete cascade,
  sender_id uuid not null references auth.users(id) on delete cascade,
  joined_at timestamptz not null default now(),
  primary key (room_code, sender_id)
);

create index if not exists chat_api_room_members_sender_idx
  on public.chat_api_room_members (sender_id, room_code);

insert into public.chat_api_rooms (room_code, room_name)
select distinct receiver_email, 'Room ' || receiver_email
from public.messages
where receiver_email ~ '^[0-9]{6}$'
on conflict (room_code) do nothing;

alter table public.chat_api_rooms enable row level security;
alter table public.chat_api_room_members enable row level security;

revoke all privileges on table public.chat_api_rooms from public, anon, authenticated;
revoke all privileges on table public.chat_api_room_members from public, anon, authenticated;

grant all privileges on table public.chat_api_rooms to service_role;
grant all privileges on table public.chat_api_room_members to service_role;
