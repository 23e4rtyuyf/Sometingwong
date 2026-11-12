-- Resolve a failed sign-in's submitted email to its target account, if any.
-- The result is only available to the server's service_role, never browser roles.

create or replace function public.lookup_chat_account_by_email(email_to_find text)
returns table(account_id uuid, account_email text)
language sql
stable
security definer
set search_path = pg_catalog, auth
as $$
  select users.id, lower(users.email::text)
  from auth.users as users
  where email_to_find is not null
    and char_length(btrim(email_to_find)) between 3 and 254
    and users.email is not null
    and lower(users.email::text) = lower(btrim(email_to_find))
  limit 1;
$$;

revoke all on function public.lookup_chat_account_by_email(text)
  from public, anon, authenticated;
grant execute on function public.lookup_chat_account_by_email(text)
  to service_role;

comment on function public.lookup_chat_account_by_email(text) is
  'Server-only exact email match for admin security logs; a match identifies the account targeted, not the actor.';

-- Backfill recent failed sign-in events when their stored email matches an account.
update public.chat_api_security_events as events
set account_id = users.id,
    account_email = lower(users.email::text)
from auth.users as users
where events.event_type = 'client_reported_sign_in_failure'
  and events.account_id is null
  and events.account_email is not null
  and lower(users.email::text) = lower(events.account_email);
