---
name: Chat account administrators
description: Authorization rule for creating confirmed Supabase accounts through the chat app.
---

Only email addresses in the private `CHAT_ADMIN_EMAILS` server setting may create other confirmed chat accounts. Verify the user's email server-side from Supabase Auth, normalize case and whitespace, and ensure the verified user matches the app session. Never commit the allowlist to public source or authorize account creation from browser-controlled values alone.

Chat sessions must also be invitation-only: accept a server-managed `app_metadata.private_chat_invited` marker set during admin account creation, an already-existing room membership for backward compatibility, or the approved admin email. Never trust `user_metadata` for authorization. Disable public Supabase sign-ups too when possible, but keep the server-side session gate because project-level signup settings can be misconfigured.

**Why:** The service-role key can create or modify any account, and Supabase accounts created outside the app can otherwise authenticate without the owner's invitation. The private admin allowlist protects account creation, the server-managed marker protects app access, and the membership fallback avoids locking out existing chat members.

**How to apply:** Keep account creation behind a server-side allowlist check, use the Supabase Auth Admin API only after authorization, set the invitation marker only through that trusted server call, and expose only a server-derived admin flag to the UI. Never store the actual email addresses in project memory or public files.
