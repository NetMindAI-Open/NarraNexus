---
code_file: scripts/migrate_to_unified_memory.py
last_verified: 2026-06-03
stub: false
---

# migrate_to_unified_memory.py — unified Agent Memory

One-shot idempotent, memrefactor-guarded migration of legacy memory tables (entity/event/narrative/chat) into the unified memory_* tables — drops vectors, backfills bi-temporal. Deterministic record_ids → re-runnable.

Part of the unified memory system (`refactor/agent-memory`). Full design,
data model, retrieval stack and migration: reference/self_notebook/specs/2026-06-03-agent-memory-unification-design.md. Mechanism vs policy split
(§3): the Engine holds the fixed lifecycle algorithm; each kind's Spec holds
policy. No vectors — recall is BM25 + grep + structured filters.
