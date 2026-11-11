---
name: Chat account administrators
description: Authorization rule for creating confirmed Supabase accounts through the chat app.
---

Only an account whose Supabase Auth `app_metadata.chat_admin` is exactly `true` may create other confirmed chat accounts. Verify that flag server-side from Supabase Auth and ensure the verified user matches the app session. Never authorize account creation from browser-supplied user metadata or a hidden UI button alone.

**Why:** `user_metadata` is user-controlled, and the service-role key can create or modify any account. The admin action must be explicitly assigned by a project owner and enforced by the Edge Function.

**How to apply:** Keep account creation behind the server-side role check and use the Supabase Auth Admin API only after authorization. Set or revoke the app metadata flag through trusted project-owner controls.
