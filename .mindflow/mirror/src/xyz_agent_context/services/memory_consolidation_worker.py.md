---
code_file: src/xyz_agent_context/services/memory_consolidation_worker.py
last_verified: 2026-07-23
stub: false
---

## 2026-07-23 — deleted-agent queue rows self-purge

`_process_scope` checks the agents row first: agent gone → DELETE every
queue row of that agent_id and return (no engine call, no state machine).
Rationale: agent deletion left queue rows behind; a successfully processed
scope returns to `dirty` with its stale `last_dirty_at`, so the idle
trigger re-fired it on EVERY 30s poll — each pass logging the
"[background-llm] no owner row" warning (prod: 1,880 warnings/14d,
bug tracker "Agent 无 owner 记录"). The delete_agent route now also
cascades the queue ([[auth.py]] 7c); this purge is the self-heal for rows
left by pre-fix deletions. Invariant for tests: a processed scope's agent
must exist (`_seed_agent` helper).

## 2026-07-03 — cost accounting: worker sets the cost context (Phase 0 / module H)

The worker runs in the backend lifespan — outside `AgentRuntime.run()`, the only
place that used to `set_cost_context`. So every consolidation LLM call resolved
`get_cost_context()==None` and recorded **zero** tokens: the largest silent hole
in token accounting (visible as the ~30s `[memory.consolidation] … falling back`
cadence). `_default_engine_consolidate` now wraps its work in
`set_cost_context(agent_id, self._db)` / `clear_cost_context()` (try/finally →
no cross-tenant bleed). **Record-only, never bill:** it sets the cost context but
deliberately NOT `current_user_id` (only `_inject_owner_credentials` →
`provider_source`), so `record_cost`'s deduct hook cannot fire — consolidation is
accounted yet never charged to the owner's free tier (iron rule #15). Adding
`set_current_user_id` here would silently drain the owner's quota — forbidden.
See [[cost_tracker]]; regression tests test_consolidation_worker_cost.py +
test_cost_tracker_deduct_hook.py. Full inventory lives in the 2026-07
token-accounting audit (author-local).

# memory_consolidation_worker.py — unified Agent Memory

Background dirty-scope consolidation worker (§7.4): count/idle/cap triggers, processing→dirty/failed state machine, per-scope failure isolation, flush_scope. Never caps the agent loop (iron rule #14).

Part of the unified memory system (`refactor/agent-memory`). The unified design covers data model, retrieval stack and migration
(author-local; the § numbers below cite its sections). Mechanism vs policy split
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

## 2026-07-07 — _inject_owner_credentials 委托到共享原语

`_inject_owner_credentials` 的 clear-first / 查 owner / resolve 序列抽到了
`provider_resolver.inject_owner_helper_credentials`（narrative updater、Step-5 hooks
现在共用同一原语）。本文件行为不变，只是不再各留一份副本（铁律 #8）。
