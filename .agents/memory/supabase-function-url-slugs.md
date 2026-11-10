---
name: Supabase function URL slugs
description: Supabase Dashboard display names and deployed function URL slugs may differ.
---

Supabase Dashboard can change a function's displayed name without changing its deployed URL slug. Do not derive the API endpoint from the display name.

**Why:** The Dashboard states that changing the name leaves the slug and endpoint URL unchanged; assuming otherwise can send app requests to a nonexistent function.

**How to apply:** Copy the function URL from the Functions list or Overview. When routing inside a function, remove the first path segment after `/functions/v1/` rather than hardcoding a particular function slug.
