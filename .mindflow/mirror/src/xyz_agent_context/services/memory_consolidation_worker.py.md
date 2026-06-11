---
code_file: src/xyz_agent_context/services/memory_consolidation_worker.py
last_verified: 2026-06-11
stub: false
---

# memory_consolidation_worker.py — unified Agent Memory

Background dirty-scope consolidation worker (§7.4): count/idle/cap triggers, processing→dirty/failed state machine, per-scope failure isolation, flush_scope. Never caps the agent loop (iron rule #14).

Part of the unified memory system (`refactor/agent-memory`). Full design,
data model, retrieval stack and migration: reference/self_notebook/specs/2026-06-03-agent-memory-unification-design.md. Mechanism vs policy split
(§3): the Engine holds the fixed lifecycle algorithm; each kind's Spec holds
policy. No vectors — recall is BM25 + grep + structured filters.

## 2026-06-11 — per-owner credential injection (cloud P0)

The worker lives in backend lifespan — OUTSIDE any HTTP request — so
auth_middleware's per-user ContextVar injection never reached it; on
cloud every consolidation LLM call fell back to the empty machine
global config and 401'd, silently (the bisect-drop amplifier, see
[[consolidate]]). `_inject_owner_credentials` now FIRST resets the ContextVars
(`api_config.clear_user_config` — without this, a scope that cannot
resolve, e.g. a deleted agent's stale queue row, silently inherits the
PREVIOUS tenant's credentials from the same worker task), then resolves
the agent owner's provider config via provider_resolver's new
`resolve_and_set_provider_for_user` before each scope's engine run:
local mode = strict no-op; quota/no-provider verdicts raise → scope
isolated as `failed` with pending_count and raw facts untouched.
Systemic failures log at ERROR (lesson #4: L2 health = useful work,
not loop-alive).

