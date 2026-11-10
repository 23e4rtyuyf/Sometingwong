# Private Live Chat

## Hosting

GitHub Pages hosts only the static page. It cannot run the Python API or protect
Supabase credentials. Keep `index.html` and `api-config.js` on GitHub Pages and
host `server.py` separately on an HTTPS Python host. Set
`window.CHAT_API_BASE_URL` in `api-config.js` to that API's HTTPS origin.

The API accepts requests only from the configured browser origins. Set
`CHAT_ALLOWED_ORIGINS` on the API host to a comma-separated list of exact
origins, for example `https://23e4rtyuyf.github.io`. Do not use `*`.

## Server secrets

Configure these on the API host, never in GitHub or `api-config.js`:

- `CHAT_ACCESS_PASSWORD`: the shared password users enter. It stays server-side;
  the browser sends a challenge proof, not the password.
- `SUPABASE_SERVICE_ROLE_KEY`: the Supabase server-only service-role key.
- `SESSION_SECRET`: a long, random value used to protect session records.

The server also uses the existing Supabase project's publishable key only for
creating the anonymous Auth users required by the existing `sender_id` column.
The browser no longer loads the Supabase client or makes Supabase requests.

The browser keeps its short-lived API token in memory only. It is not written to
local storage, so users sign in again after a reload or in a new tab. Encrypted
chat history and the existing room/profile data remain local to each device.
Message contents are encrypted in the browser; the server receives ciphertext.
The server polls Supabase for updates instead of exposing Supabase Realtime to
the browser.

Choose a long, random shared password. The challenge proof never sends the
password itself, but an intercepted proof can be checked against guesses; the
PBKDF2 work factor slows those guesses but does not replace a strong password.

## Lock down direct Supabase access

The old setup allowed anonymous browser requests to read and write all rooms.
The following approved SQL closes that direct database path while allowing
the server's service-role key to operate:

```sql
alter table public.messages enable row level security;
revoke all privileges on table public.messages from anon, authenticated, public;
grant select, insert on table public.messages to service_role;
```

This SQL has been applied in Supabase. A zero-row permission check confirmed
anonymous reads are denied and the service role can still read; no message
contents or test rows were accessed.

Do not restore public `anon` read/insert grants or policies for `messages`.
Direct anonymous Auth signup remains enabled only because the server creates a
guest Auth user for the existing `sender_id` column.

## Local Replit preview

The configured **Start application** workflow runs:

```sh
python3 server.py
```

It serves the page and API on port 5000. For local preview, leave
`CHAT_API_BASE_URL` empty. Ensure `CHAT_ALLOWED_ORIGINS` includes the current
HTTPS Replit preview origin. Replit does not automatically configure Supabase
or apply database permissions.
