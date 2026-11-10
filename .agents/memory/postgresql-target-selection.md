---
name: PostgreSQL target selection
description: Avoid accidentally querying Replit's local PostgreSQL when intending to access an external database.
---

The Replit shell may configure PostgreSQL environment variables that cause `psql` without an explicit connection target to use the local Replit database.

**Why:** A successful `psql` query without a target does not prove a Supabase connection succeeded.

**How to apply:** For external SQL work, use and verify the explicit external connection target. Do not infer the target from a bare `psql` success.
