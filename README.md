# Private Live Chat

Run this migration once in Supabase **SQL Editor**:

```sql
alter table public.messages
  add column if not exists room_code text,
  add column if not exists sender_name text;

alter table public.messages
  alter column sender_id drop not null;

alter table public.messages
  alter column sender_email drop not null,
  alter column receiver_email drop not null;

update public.messages
set room_code = 'LEGACY', sender_name = coalesce(sender_email, 'friend')
where room_code is null;

alter table public.messages
  alter column room_code set not null,
  alter column sender_name set not null;

drop policy if exists "send own messages" on public.messages;
drop policy if exists "read sent or received messages" on public.messages;
drop policy if exists "users can send messages" on public.messages;
drop policy if exists "users can read their messages" on public.messages;

create policy "anyone can send to a room"
on public.messages for insert to anon, authenticated
with check (
  room_code ~ '^[A-Z0-9]{6,12}$'
  and length(sender_name) between 1 and 40
);

create policy "anyone can read a room"
on public.messages for select to anon, authenticated
using (room_code ~ '^[A-Z0-9]{6,12}$');

alter publication supabase_realtime add table public.messages;
```

The app stores only `PM3` ciphertext. The room code is not encryption, so
share a strong passphrase separately. Anyone who knows a room code can see
that room's ciphertext, but cannot decrypt it without the passphrase.
