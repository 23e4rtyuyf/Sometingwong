---
name: Chat identity across sign-ins
description: Supabase Auth accounts provide stable sender identity while chat encryption uses a separate shared passphrase.
---

Use the Supabase Auth account UUID as the stable sender identity. Account email/password authenticates the user; keep the shared chat-encryption passphrase separate and never send it to the chat API. Exchange the Supabase access token for a short-lived app session without persisting the Supabase token in the browser. Provision confirmed accounts through the Supabase dashboard, and disable public sign-ups when access should be limited to accounts added by the owner.

**Why:** Users need durable ownership across devices without exposing the shared encryption passphrase to the server. This does not require a new database table. Messages created under the old anonymous-user identities are not automatically owned by the new account UUID.

**How to apply:** Keep browser sign-in, Edge Function token verification, and the local server aligned. Reject anonymous Supabase users from app sessions and verify ownership behavior before migrating any legacy messages.
