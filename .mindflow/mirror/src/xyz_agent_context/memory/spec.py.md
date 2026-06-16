---
code_file: src/xyz_agent_context/memory/spec.py
last_verified: 2026-06-08
stub: false
---

## 2026-06-08 — `passive` flag splits the two recall surfaces

Added `passive: bool` to MemoryKindSpec plus a `passive_kinds()` helper. Passive kinds (observation/entity/narrative) are the distilled knowledge auto-injected EVERY turn by GeneralMemory's hook; the `remember` / `grep_memory` TOOLS instead span ALL searchable kinds (incl. interaction/job/bus). This split is the mechanism behind removing chat-echo pollution from passive injection while keeping everything explicitly searchable. See [[specs]] for the per-kind flags.

# spec.py — unified Agent Memory

MemoryKindSpec (per-kind policy: dedup_key, merge, prompts, recall weights, render, evict) + the kind registry. The 'policy' half of mechanism-vs-policy.

Part of the unified memory system (`refactor/agent-memory`). Full design,
data model, retrieval stack and migration: reference/self_notebook/specs/2026-06-03-agent-memory-unification-design.md. Mechanism vs policy split
(§3): the Engine holds the fixed lifecycle algorithm; each kind's Spec holds
policy. No vectors — recall is BM25 + grep + structured filters.
