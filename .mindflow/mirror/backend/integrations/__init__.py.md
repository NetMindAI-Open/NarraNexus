---
code_file: backend/integrations/__init__.py
last_verified: 2026-07-24
stub: false
---

# backend/integrations/__init__.py — platform clients live app-side

## Why it exists

Bucket-A of the platform/agent split (2026-07-24): the NetMind client
group and Arena provisioning sat inside `src/xyz_agent_context` although
no agent-side process imported them (verified: consumers were 4 backend
routes + scripts/data_migrations/migrate_users_to_netmind.py). Under the placement rule —
*the agent package holds only what agent-side processes must import* —
they belong to the API app, so they moved here.

## Design decisions

- **Dependency direction is one-way**: backend → xyz_agent_context is
  allowed (these clients import package utils/settings); the agent
  package never imports backend.
- **Worker/MCP litmus test**: before moving anything else here, grep who
  imports it — if a worker or MCP/module process consumes it, it must
  stay in the package (that is why `feedback_client` did not move).
