---
name: Chat identity across sign-ins
description: The private chat uses a stable per-browser sender identity while retaining password-gated anonymous sessions.
---

Keep a random identity token in the browser and map its server-side hash to the same Supabase anonymous user. Reuse the existing session table for this mapping, and reject far-future identity rows as bearer sessions.

**Why:** Password-only sign-ins create a new anonymous user each time, which hides Delete on that user's earlier messages. The mapping avoids a database schema change and does not treat a nickname as proof of ownership.

**How to apply:** Only resolve the identity after the password proof succeeds. Keep the identity token separate from the short-lived API bearer token, and update the browser, Edge Function, and local development server together if the identity mechanism changes.
