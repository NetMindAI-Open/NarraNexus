---
code_file: backend/integrations/netmind/__init__.py
last_verified: 2026-07-24
stub: false
---

# backend/integrations/netmind/__init__.py — NetMind platform client group

Anchor for the NetMind-facing clients moved out of `services/` in the
2026-07-24 layout cleanup: `netmind_auth_client`, `netmind_billing_client`,
`netmind_key_client`, `netmind_provisioner`, `power_account`, and
`identity_migration` (live login-path service, hence here rather than
`migrations/`). Inert `__init__` — consumers import modules explicitly.
