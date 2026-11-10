---
name: GitHub Pages hosting
description: The chat frontend is intended for GitHub Pages, so server behavior must run on a separate API host.
---

The user said this project will be hosted on GitHub Pages.

**Why:** GitHub Pages only serves static assets; it cannot run the Python chat API.

**How to apply:** Keep frontend assets static, configure an explicit HTTPS API origin, and do not assume `server.py` runs as part of GitHub Pages.
