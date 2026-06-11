---
code_file: src/xyz_agent_context/memory/_memory_impl/repository.py
last_verified: 2026-06-03
stub: false
---

# repository.py — unified Agent Memory

MemoryRepository(BaseRepository[MemoryRecord]) — generic per-kind store; scope/bi-temporal query, tombstone-supersession, recency touch. Reuses N+1 batch loading.

Part of the unified memory system (`refactor/agent-memory`). Full design,
data model, retrieval stack and migration: reference/self_notebook/specs/2026-06-03-agent-memory-unification-design.md. Mechanism vs policy split
(§3): the Engine holds the fixed lifecycle algorithm; each kind's Spec holds
policy. No vectors — recall is BM25 + grep + structured filters.
