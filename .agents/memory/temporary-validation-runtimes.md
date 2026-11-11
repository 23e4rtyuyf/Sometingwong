---
name: Temporary validation runtimes
description: Prevent one-off JavaScript validation from changing the Python application's runtime setup.
---

Running a one-off Node check can automatically add a Node module to the Replit configuration.

**Why:** JavaScript-only validation introduced an unrelated runtime change alongside the Supabase routing fix.

**How to apply:** Check configuration changes after temporary runtime use. Remove an unintended module through the package-management callback, not by directly editing `.replit`, which is protected.
