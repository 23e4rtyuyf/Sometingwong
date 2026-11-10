# Move the chat API to Supabase

The GitHub Pages app currently points to the Replit API. This Edge Function is
the approved-host alternative for networks that block `replit.app`. The app is
not switched to it yet; keep using the current API until the function is
deployed and its health check succeeds.

## 1. Add the private state tables

In the Supabase project's SQL Editor, run
`supabase/migrations/20261110000100_chat_api_state.sql`.

The migration adds only private tables for one-time sign-in challenges,
failed-login rate limits, and server sessions. It does not change or delete
the existing `messages` table. The new tables deny access to `anon` and
`authenticated`; only the function's service-role key can use them.

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

## 4. Check the function before switching the site

The function's base URL is the URL shown in the Functions list. The Dashboard's
editable **Name** can differ from the deployed URL slug; changing the name does
not rename the endpoint. This project's deployed URL is:

```text
https://iozdwksqfhlhcrbhrwfl.supabase.co/functions/v1/swift-endpoint
```

The function source handles subpaths independently of that slug. Before
changing `api-config.js`, verify that `/api/health` returns
`{"ok":true,"storageConfigured":true,"authConfigured":true}` and that
`/api/auth/challenge` returns HTTP 200 from the GitHub Pages origin.

After those checks pass, update `api-config.js` to use the function base URL
and the public Supabase publishable key. Never use the service-role key in
`api-config.js`. The browser client supports a public `CHAT_API_KEY` header for
the Supabase gateway.
