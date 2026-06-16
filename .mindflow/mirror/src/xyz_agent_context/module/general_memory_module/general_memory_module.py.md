---
code_file: src/xyz_agent_context/module/general_memory_module/general_memory_module.py
last_verified: 2026-06-08
stub: false
---

## 2026-06-08 — passive injection uses passive_kinds()

`hook_data_gathering` now recalls over `passive_kinds()` (observation/entity/narrative — distilled knowledge) instead of all kinds, so chat/event/job/bus no longer pollute the per-turn passive injection. They stay explicitly searchable via the `remember` tool. See [[spec]] / [[specs]].

# general_memory_module.py — unified Agent Memory

GeneralMemoryModule — learns world/experience observations each turn (hook_after_event) and injects cross-kind unified memory into context (hook_data_gathering via remember). The single point unified memory feeds the agent loop.

Part of the unified memory system (`refactor/agent-memory`). Full design,
data model, retrieval stack and migration: reference/self_notebook/specs/2026-06-03-agent-memory-unification-design.md. Mechanism vs policy split
(§3): the Engine holds the fixed lifecycle algorithm; each kind's Spec holds
policy. No vectors — recall is BM25 + grep + structured filters.

**Timestamps as interim supersession (2026-06-05).** A memory about the same
thing changes over time, but the real update/supersession path is deferred
(too complex for now). As a stopgap, each injected memory line carries a
`(YYYY-MM-DD HH:MM)` stamp (`_recalled_at`: `record.updated_at or created_at`,
UTC) and `get_instructions` tells the agent to trust the most recent when two
memories disagree. This is a SOFT, render-time hint for the LLM — not hard
dedup: observations stay append-only, so conflicting versions can both surface
in recall and the agent picks the latest by timestamp. The timestamps already
existed on `MemoryRecord` (created_at column DEFAULT); this change only
surfaces them in the passive-injection path (the `remember`/`grep_memory`
tools already returned `when`).
