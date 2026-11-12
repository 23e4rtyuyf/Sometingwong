-- Add limited raw IP and account identifiers to the private admin security log.
-- Passwords, tokens, message bodies, and message ciphertext are not stored here.

alter table public.chat_api_security_events
  add column if not exists client_ip inet,
  add column if not exists account_id uuid,
  add column if not exists account_email text
    check (account_email is null or char_length(account_email) between 3 and 254);

create index if not exists chat_api_security_events_client_ip_created_idx
  on public.chat_api_security_events (client_ip, created_at desc);

create index if not exists chat_api_security_events_account_id_created_idx
  on public.chat_api_security_events (account_id, created_at desc);

create index if not exists chat_api_security_events_account_email_created_idx
  on public.chat_api_security_events (account_email, created_at desc);

comment on column public.chat_api_security_events.client_ip is
  'Raw client IP; restricted to service-role access and admin-authenticated log views.';
comment on column public.chat_api_security_events.account_id is
  'Verified Supabase account ID when a valid app session is available.';
comment on column public.chat_api_security_events.account_email is
  'Verified account email, or unverified browser-reported email for failed sign-ins.';
