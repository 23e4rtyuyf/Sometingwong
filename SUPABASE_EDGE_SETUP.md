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

These migrations add private tables without changing or deleting the existing
`messages` table. The new tables deny access to `anon` and `authenticated`;
only the function's service-role key can use them. Room messages are readable
or writable only after the current session joins that room.

## 2. Add function secrets

In Supabase Dashboard, open **Edge Functions → Secrets** and set:

- `SESSION_SECRET`

Use the same `SESSION_SECRET` value currently stored in Replit Secrets. Enter it
only in Supabase's secret form, never in this repository or chat. Supabase
injects its project URL, publishable key, and service-role key into the function
runtime; the service-role key must never be placed in GitHub Pages.

## 3. Approve account administrators

Only accounts marked as administrators can create additional accounts. To mark
your own account, replace `YOUR_ACCOUNT_EMAIL` with the email you use to sign
in, then run this once in the Supabase SQL Editor:

```sql
update auth.users
set raw_app_meta_data =
  coalesce(raw_app_meta_data, '{}'::jsonb) || '{"chat_admin": true}'::jsonb
where lower(email) = lower('YOUR_ACCOUNT_EMAIL');
```

The result should report one updated row. Sign out and sign in again. The
**Add account** button will appear. New accounts are created as confirmed users
and do not receive a verification email. Share their temporary passwords
privately. Do not grant this administrator flag to regular accounts.

## 4. Deploy the function

The source is `supabase/functions/chat-api/index.ts`. For a version-controlled
deployment, use the Supabase CLI from a trusted terminal:

```sh
supabase login
supabase link --project-ref iozdwksqfhlhcrbhrwfl
supabase functions deploy chat-api --no-verify-jwt
```

The `verify_jwt = false` setting in `supabase/config.toml` is intentional:
Supabase's gateway must pass the request to the function, where this app checks
the signed-in Supabase account and its server-side session. Do not remove those
checks or expose the service-role key.

The Supabase Dashboard also supports deploying a function through its editor.
If using it, paste the versioned function source above and turn off **Verify
JWT** for this function. The Dashboard editor does not keep its edits in this
GitHub repository.

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

`api-config.js` uses the function base URL and public Supabase publishable key.
Never put the service-role key in browser code. The browser client sends the
public key in the `apikey` header for the Supabase gateway.
