---
code_file: src/narranexus/cli/distribution_scaffold.py
last_verified: 2026-09-04
stub: false
---

# cli/distribution_scaffold.py — create-app

`create_app(dist_id, dest, display_name, base, auth, deployment)` writes `narranexus-dist.json` (the official `base` distribution's builtin set minus the auth builtins, plus the chosen `auth` — a builtin by range or a scaffolded stub by path — and a bundled `<publisher>.<name>_core` plugin scaffolded from the `hook` template), `branding/logo.svg`, `defaults/agent.json`, a README and `.github/workflows/dist.yml` (dist doctor + build --dry-run). Rejects non `<publisher>.<name>` ids and the builtin./narranexus. prefixes.
