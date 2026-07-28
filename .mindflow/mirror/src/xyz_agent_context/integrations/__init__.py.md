---
code_file: src/xyz_agent_context/integrations/__init__.py
last_verified: 2026-07-24
stub: false
---

# integrations/__init__.py — external-platform clients, out of services/

## Why it exists

`services/` (defined as "background pollers/workers" in CLAUDE.md) had
accumulated 8 external-platform client modules — NetMind auth/billing/key
provisioning, power-account, Arena provisioning, feedback intake, identity
migration — until half the directory wasn't background services at all.
The 2026-07-24 layout cleanup split them out: `integrations/<platform>/`
holds clients that talk OUT to external platforms; `services/` keeps only
long-running in-process workers (module_poller, instance_sync, message_bus
poller, memory consolidation, model_sync_runner, alerts, audit, skill sync).

## Design decisions

- **Grouped by platform** (`netmind/`, `arena/`), single-file clients stay
  flat (`feedback_client.py`).
- **`identity_migration` lives under `netmind/`**, not `migrations/` — it is
  a live login-path service tied to the NetMind user system, not a one-shot
  data migration.
- **No re-exports**, mirroring `marketplace/__init__.py`: consumers already
  import modules explicitly; the split stays a pure move.
- **Deploy contract untouched**: the two compose/Makefile entrypoints in
  `services/` (`model_sync_runner`, `module_poller`) did not move.

## 2026-07-24 — netmind/ + arena/ moved to backend/integrations/

Bucket-A platform split: the NetMind group (6 modules) and Arena
provisioning had ZERO agent-side imports (consumers were 4 backend routes
+ 1 ops script), so they moved to `backend/integrations/`. Only
`feedback_client.py` stays — the agent's `submit_feedback` MCP tool calls
it from the module process, so it must remain importable from the agent
package. Placement rule recorded in the package docstring.
