# Private Messages

The GitHub Pages app encrypts messages in the browser with AES-256-GCM before
storing ciphertext in Supabase. It uses Supabase email authentication.

## Supabase setup

In Supabase **SQL Editor**, run:

```sql
create table if not exists public.messages (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  sender_id uuid not null references auth.users(id) on delete cascade,
  sender_email text not null,
  receiver_email text not null,
  encrypted_text text not null
);

alter table public.messages enable row level security;

create policy "users can send messages"
on public.messages for insert to authenticated
with check (sender_id = auth.uid());

create policy "users can read their messages"
on public.messages for select to authenticated
using (
  sender_id = auth.uid()
  or lower(receiver_email) = lower(coalesce(auth.jwt()->>'email', ''))
);
```

In **Authentication → Providers**, enable Email. The app uses the project's
public URL and anon key; never put the `service_role` key in the browser.
