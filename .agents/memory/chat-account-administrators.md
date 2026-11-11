---
name: Chat account administrators
description: Authorization rule for creating confirmed Supabase accounts through the chat app.
---

Only email addresses in the private `CHAT_ADMIN_EMAILS` server setting may create other confirmed chat accounts. Verify the user's email server-side from Supabase Auth, normalize case and whitespace, and ensure the verified user matches the app session. Never commit the allowlist to public source or authorize account creation from browser-controlled values alone.

**Why:** The service-role key can create or modify any account, and public source should not expose the owner's allowlist. An explicit private allowlist limits this capability to the accounts the owner chose.

**How to apply:** Keep account creation behind a server-side allowlist check, use the Supabase Auth Admin API only after authorization, and expose only a server-derived admin flag to the UI. Never store the actual email addresses in project memory or public files.
