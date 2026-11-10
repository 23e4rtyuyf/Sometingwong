# Private Live Chat

The app uses the existing `messages` columns, so no `sender_name` column is
needed. Run this in Supabase **SQL Editor**:

```sql
alter table public.messages enable row level security;

drop policy if exists "send own messages" on public.messages;
drop policy if exists "read sent or received messages" on public.messages;
drop policy if exists "users can send messages" on public.messages;
drop policy if exists "users can read their messages" on public.messages;
drop policy if exists "anyone can send to a room" on public.messages;
drop policy if exists "anyone can read a room" on public.messages;

create policy "chat can send encrypted messages"
on public.messages for insert
to anon, authenticated
with check (
  length(sender_email) between 1 and 40
  and receiver_email ~ '^[A-Z0-9]{6,12}$'
  and length(encrypted_text) > 30
);

create policy "chat can read encrypted messages"
on public.messages for select
to anon, authenticated
using (receiver_email ~ '^[A-Z0-9]{6,12}$');

do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'public'
      and tablename = 'messages'
  ) then
    alter publication supabase_realtime add table public.messages;
  end if;
end $$;
```

In this simple design, `sender_email` stores the nickname and `receiver_email`
stores the room code. Only encrypted ciphertext is stored; the passphrase is
never sent to Supabase. A strong passphrase protects message contents, but no
public website can be guaranteed impossible to hack.

Enable **Authentication → Providers → Anonymous Sign-Ins** in Supabase.
The app creates a hidden guest session automatically; users do not create or
enter an account. This is required because the existing table requires
`sender_id`.
