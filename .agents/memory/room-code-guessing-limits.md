---
name: Room-code guessing limits
description: Current protections and the remaining risk from short chat room codes.
---

Use separate HMAC namespaces for IP-based and sender-based failed room-join counters, stored in the existing failed-login table. This gives basic protection without exposing raw IP addresses or adding a migration.

**Why:** Existing rooms use six-digit codes and users expect to join with those codes. Throttling slows basic guessing but cannot make a short code equivalent to a strong invitation.

**How to apply:** Keep the counters separate from password failures. For stronger room privacy, replace short codes with high-entropy invitation secrets and explicit membership approval; that requires a planned schema and UI change.
