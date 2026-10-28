---
code_file: backend/auth_provider.py
last_verified: 2026-09-07
stub: false
---

# backend/auth_provider.py — the bound kernel.auth provider

`bound_provider_id()` = the distribution's `auth` when `NARRANEXUS_DIST` is set, else the builtin matching the middleware's mode predicate (`builtin.auth.netmind` on cloud, `builtin.auth.local` locally). `auth_provider()` resolves that id in the `kernel.auth` registry (registering the builtin providers from their manifests on first use), builds the instance once per plugin id, checks it is an `AuthProvider`, and fails closed with `RuntimeError` when nothing is bound. `reset_auth_provider()` is for tests.

2026-09-07: a non-default `kernel.auth` binding (distribution layer) wins over the distribution `auth` / deployment-mode fallback.

## 2026-09-07 — no lazy provider registration

auth_provider() reads the booted kernel.auth registry; _ensure_builtin_providers is gone (it could resurrect an excluded auth provider).
