---
name: Room-code guessing limits
description: Current protections and the remaining risk from short chat room codes.
---

Use separate HMAC namespaces for IP-based and sender-based failed room-join counters, stored in the existing failed-login table. Keep raw client IPs and account identifiers in the private security-event log, visible only through admin-authenticated server endpoints. Do not log passwords, tokens, or message contents; mark browser-reported sign-in emails as unverified.

**Why:** Existing rooms use six-digit codes and users expect to join with those codes. The user requested raw IPs and account identifiers in admin security logs to investigate security events, while keeping credentials and message content out of logs.

**How to apply:** Keep the counters separate from password failures. Retain security events for 30 days and label submitted emails from failed sign-ins as unverified. For stronger room privacy, replace short codes with high-entropy invitation secrets and explicit membership approval; that requires a planned schema and UI change.
