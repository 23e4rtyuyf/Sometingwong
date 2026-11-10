# Private Live Chat

## Run on Replit

Click **Run** to start the **Start application** workflow, which runs
`python3 server.py` on `0.0.0.0:5000`. The server uses only Python's standard
library. It serves the public files and the authenticated chat API.

## GitHub Pages and the API

GitHub Pages serves the static frontend only. The Python API must run on a
separate HTTPS host. Configure that origin in `api-config.js` and set the exact
GitHub Pages origin in the API host's `CHAT_ALLOWED_ORIGINS` environment
variable. Never use wildcard CORS.

The browser must not call Supabase directly. Keep `SUPABASE_SERVICE_ROLE_KEY`,
`CHAT_ACCESS_PASSWORD`, and `SESSION_SECRET` in the API host's secrets manager.
Never place them in GitHub, `api-config.js`, or browser code. The existing
Supabase publishable key is used by the server only to create anonymous Auth
users for the existing `sender_id` column.

The server-side access proof avoids sending the password itself. API tokens are
short-lived and held only in browser memory; a reload or new tab requires
signing in again. Message encryption stays in the browser, and the server
stores/forwards ciphertext only.

The approved Supabase permission lock-down in README.md has been applied.
Anonymous `messages` reads are denied and the service role can still read.
Anonymous Auth sign-ins must remain enabled for server-created guest users.

Browser encryption, clipboard access, and secure random IDs require HTTPS.
