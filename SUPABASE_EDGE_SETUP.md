# Supabase chat API setup

The GitHub Pages app uses this Supabase Edge Function for chat and message
storage. The function keeps service credentials on the server and requires the
configured chat password before issuing a session.

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

- `CHAT_ACCESS_PASSWORD`
- `SESSION_SECRET`

Use the same values currently stored in Replit Secrets. Enter them only in
Supabase's secret form, never in this repository or chat. Supabase injects its
project URL, publishable key, and service-role key into the function runtime;
the service-role key must never be placed in GitHub Pages.

## 3. Deploy the function

The source is `supabase/functions/chat-api/index.ts`. For a version-controlled
deployment, use the Supabase CLI from a trusted terminal:

```sh
supabase login
supabase link --project-ref iozdwksqfhlhcrbhrwfl
supabase functions deploy chat-api --no-verify-jwt
```

The `verify_jwt = false` setting in `supabase/config.toml` is intentional:
Supabase's gateway must pass the request to the function, where this app's own
password challenge and session checks run. Do not remove those checks or expose
the service-role key.

The Supabase Dashboard also supports deploying a function through its editor.
If using it, paste the versioned function source above and turn off **Verify
JWT** for this function. The Dashboard editor does not keep its edits in this
GitHub repository.

## 4. Check the function and room features

The function's base URL is the URL shown in the Functions list. The Dashboard's
editable **Name** can differ from the deployed URL slug; changing the name does
not rename the endpoint. This project's deployed URL is:

```text
https://iozdwksqfhlhcrbhrwfl.supabase.co/functions/v1/swift-endpoint
```

The function source handles subpaths independently of that slug. Verify that
`/api/health` returns
`{"ok":true,"storageConfigured":true,"authConfigured":true}` and that
`/api/auth/challenge` returns HTTP 200 from the GitHub Pages origin. Then create
a room, rename it, and join it from a second browser to confirm the shared name
and membership checks work.

`api-config.js` uses the function base URL and public Supabase publishable key.
Never put the service-role key in browser code. The browser client sends the
public key in the `apikey` header for the Supabase gateway.
