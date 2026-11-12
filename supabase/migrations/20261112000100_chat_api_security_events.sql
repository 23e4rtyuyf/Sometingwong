-- Private, metadata-only security activity for the chat API.
-- Raw IP addresses, credentials, message bodies, and room codes are not stored.

create table if not exists public.chat_api_security_events (
  event_id text primary key,
  created_at timestamptz not null default now(),
  event_type text not null check (event_type in (
    'client_reported_sign_in_failure',
    'unauthorized_request',
    'forbidden_request',
    'rate_limited_request',
    'invalid_room_code',
    'invalid_admin_request',
    'unknown_route',
    'server_error'
  )),
  request_method text not null check (request_method in ('GET', 'POST')),
  request_path text not null check (
    char_length(request_path) between 1 and 120
    and request_path like '/api/%'
  ),
  status_code smallint not null check (status_code between 400 and 599),
  address_hash text
);

create index if not exists chat_api_security_events_created_idx
  on public.chat_api_security_events (created_at desc);

create index if not exists chat_api_security_events_address_created_idx
  on public.chat_api_security_events (address_hash, created_at desc);

alter table public.chat_api_security_events enable row level security;

revoke all privileges on table public.chat_api_security_events
  from public, anon, authenticated;
grant all privileges on table public.chat_api_security_events to service_role;
