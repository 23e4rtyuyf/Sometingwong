-- Private state for the chat API hosted in a Supabase Edge Function.
-- The function uses the service-role key; browsers must not access these tables.

create table if not exists public.chat_api_challenges (
  challenge_id text primary key,
  nonce text not null,
  address_hash text not null,
  expires_at timestamptz not null,
  created_at timestamptz not null default now()
);

create index if not exists chat_api_challenges_address_expiry_idx
  on public.chat_api_challenges (address_hash, expires_at);

create table if not exists public.chat_api_failed_logins (
  attempt_id text primary key,
  address_hash text not null,
  attempted_at timestamptz not null default now()
);

create index if not exists chat_api_failed_logins_address_time_idx
  on public.chat_api_failed_logins (address_hash, attempted_at);

create table if not exists public.chat_api_sessions (
  token_hash text primary key,
  sender_id uuid not null references auth.users(id) on delete cascade,
  expires_at timestamptz not null,
  created_at timestamptz not null default now()
);

create index if not exists chat_api_sessions_expiry_idx
  on public.chat_api_sessions (expires_at);

alter table public.chat_api_challenges enable row level security;
alter table public.chat_api_failed_logins enable row level security;
alter table public.chat_api_sessions enable row level security;

revoke all privileges on table public.chat_api_challenges from public, anon, authenticated;
revoke all privileges on table public.chat_api_failed_logins from public, anon, authenticated;
revoke all privileges on table public.chat_api_sessions from public, anon, authenticated;

grant all privileges on table public.chat_api_challenges to service_role;
grant all privileges on table public.chat_api_failed_logins to service_role;
grant all privileges on table public.chat_api_sessions to service_role;
