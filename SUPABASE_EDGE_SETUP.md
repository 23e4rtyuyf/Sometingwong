# Supabase chat API setup

The GitHub Pages app uses this Supabase Edge Function for chat and message
storage. The function keeps service credentials on the server and requires a
confirmed Supabase account before issuing a session.

## 1. Add the private state tables

The initial server-side sign-in setup uses
`supabase/migrations/20261110000100_chat_api_state.sql`. The shared room names
and room membership feature additionally requires
`supabase/migrations/20261111000100_chat_api_rooms.sql`. Run the new room
migration in the Supabase SQL Editor.

The admin-only security activity panel additionally requires
`supabase/migrations/20261112000100_chat_api_security_events.sql`. Run it in
the Supabase SQL Editor before deploying the updated Edge Function.
To include raw client IPs and account identifiers in the admin log, also run
`supabase/migrations/20261112000200_chat_api_security_event_identifiers.sql`
after the first security-events migration and before deploying the updated
function.

These migrations add private tables without changing or deleting the existing
`messages` table. The new tables deny access to `anon` and `authenticated`;
only the function's service-role key can use them. Room messages are readable
or writable only after the current session joins that room.

## 2. Add function secrets

In Supabase Dashboard, open **Edge Functions → Secrets** and set:

- `SESSION_SECRET`
- `CHAT_ADMIN_EMAILS`

Use the same `SESSION_SECRET` value currently stored in Replit Secrets. Set
`CHAT_ADMIN_EMAILS` to the comma-separated exact email addresses allowed to add
accounts. Keep these addresses in the secret settings, not in this public
repository. Add the same `CHAT_ADMIN_EMAILS` secret in Replit if using the local
Python server. Supabase injects its project URL, publishable key, and
service-role key into the function runtime; the service-role key must never be
placed in GitHub Pages.

## 3. Restrict account administrators

Only email addresses in `CHAT_ADMIN_EMAILS` can create additional accounts.
The function checks this list server-side both when signing in and when adding
an account. No database change is needed to set administrators. Keep the list
limited to the exact accounts you intend to authorize.

After saving the secret, sign in again. The **Add account** button will appear
for an allowlisted account. New accounts are created as confirmed users and do
not receive a verification email. Share their temporary passwords privately.

## 4. Deploy the function

The source is `supabase/functions/chat-api/index.ts`. This app calls the
existing deployed URL ending in `/functions/v1/swift-endpoint`; update that
existing function rather than creating a second function. In the Dashboard,
open **Edge Functions → Functions → swift-endpoint → Code**, replace the code
with the versioned source, then save and deploy it.

The `verify_jwt = false` setting in `supabase/config.toml` is intentional:
Supabase's gateway must pass the request to the function, where this app checks
the signed-in Supabase account and its server-side session. Do not remove those
checks or expose the service-role key.

Keep **Verify JWT** off for this function. The Dashboard editor does not keep
its edits in this GitHub repository. Avoid deploying `chat-api` under a new
slug unless you also change `api-config.js` to the new endpoint.

## 5. Check the function and room features

The function's base URL is the URL shown in the Functions list. The Dashboard's
editable **Name** can differ from the deployed URL slug; changing the name does
not rename the endpoint. This project's deployed URL is:

```text
https://iozdwksqfhlhcrbhrwfl.supabase.co/functions/v1/swift-endpoint
```

The function source handles subpaths independently of that slug. Confirm
`/api/health` returns
`{"ok":true,"storageConfigured":true,"authConfigured":true}`. Then open the
GitHub Pages app, sign in, create two rooms, and confirm both appear in the chat
list. An account administrator can also create a confirmed account through
**Add account**.

## 6. Review security activity

After running both security-event migrations and deploying the updated function,
an allowlisted administrator can open **Security log** in the app. It shows
recent API security events, raw client IPs, and account IDs/emails when available.
Failed sign-in email addresses are browser-reported and unverified. Passwords,
access passwords, tokens, room codes, and message contents are not included.
The new IP and account columns remain protected by the existing service-role-only
table permissions and the admin-only endpoint.
Supabase's full Edge Function runtime logs remain available in the Dashboard
under **Edge Functions → Functions → your function → Logs**.
Individual event deletion uses the same admin-only endpoint and existing
service-role table permissions. Unread badges count messages without returning
their contents and save each account's read cursor on the current device. These
features do not require another SQL migration.

`api-config.js` uses the function base URL and public Supabase publishable key.
Never put the service-role key in browser code. The browser client sends the
public key in the `apikey` header for the Supabase gateway.
