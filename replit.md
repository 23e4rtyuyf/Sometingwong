# Private Live Chat

## Run on Replit

Click **Run** to start the **Start application** workflow, which runs
`python3 server.py` on `0.0.0.0:5000`. Open Preview to use the app.
The server uses only Python's standard library; no package installation is
required. It serves only `index.html` and `logo.png`, not project internals.

## Existing app and external services

The imported app remains plain HTML, CSS, and browser JavaScript in `index.html`.
The Supabase browser library loads from jsDelivr. The existing Supabase URL and
public browser key remain unchanged. Never put a Supabase service-role key in
the page.

The initial screen requires the existing access password. Its hash is in the
imported code; the plaintext is not provided by the repository. Keep this gate
in place. This client-side gate is not server-side authentication.

Live chat depends on the existing Supabase project being available, anonymous
sign-ins being enabled, and the `messages` table having the columns, policies,
and realtime publication described in README.md. Replit does not automatically
apply those SQL statements or configure Supabase. Do not replace the database
or change its policies as part of routine startup.

Browser encryption, clipboard access, and secure random IDs require a secure
context; use Replit's HTTPS Preview URL.
