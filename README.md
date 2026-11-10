# Private Messages

Run this once in Supabase **SQL Editor**:

```sql
create table public.messages (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  sender_id uuid not null references auth.users(id) on delete cascade,
  sender_email text not null,
  receiver_email text not null,
  encrypted_text text not null
);

alter table public.messages enable row level security;

create policy "send own messages"
on public.messages for insert to authenticated
with check (sender_id = auth.uid());

create policy "read sent or received messages"
on public.messages for select to authenticated
using (
  sender_id = auth.uid()
  or lower(receiver_email) = lower(coalesce(auth.jwt()->>'email', ''))
);
```

Then enable **Authentication → Providers → Email** in Supabase.

The app stores only `PM3` ciphertext in Supabase. The encryption passphrase
never leaves the browser. The browser uses only the Supabase project URL and
publishable key; never put a `service_role` key in the page.
