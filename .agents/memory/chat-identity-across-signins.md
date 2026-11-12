---
name: Chat identity across sign-ins
description: Supabase Auth accounts provide stable sender identity while chat encryption uses a separate shared passphrase.
---

Use the Supabase Auth account UUID as the stable sender identity. Store each person's chat display name in their Supabase Auth `user_metadata` so it follows their account across devices; keep room codes and encrypted chat history in their existing storage. Unread read-cursors are keyed by account in local storage and remain device-specific; they do not provide cross-device read receipts. Account email/password authenticates the user; keep the shared chat-encryption passphrase separate and never send it to the chat API. Exchange the Supabase access token for a short-lived app session without persisting the Supabase token in the browser. Provision confirmed accounts through the Supabase dashboard, and disable public sign-ups when access should be limited to accounts added by the owner.

**Why:** Users need durable ownership and a consistent display name across devices without exposing the shared encryption passphrase to the server. Supabase Auth metadata avoids adding a chat-profile table or changing SQL. Local unread cursors avoid a read-receipt migration; the user did not request unread-state sync across devices. Messages created under the old anonymous-user identities are not automatically owned by the new account UUID.

**How to apply:** Read the display name from the signed-in Supabase Auth user and update only that user's metadata using their access token. Treat that metadata as user-controlled display text, never as trusted authorization data. Keep browser sign-in, Edge Function token verification, and the local server aligned. Reject anonymous Supabase users from app sessions and verify ownership behavior before migrating any legacy messages.
