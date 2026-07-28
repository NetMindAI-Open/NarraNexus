---
code_file: src/xyz_agent_context/memory/coordinator.py
last_verified: 2026-06-03
stub: false
---

# coordinator.py — unified Agent Memory

MemoryCoordinator facade — cross-kind remember() (RRF-fused ranked recall) and grep_memory() (exact/regex). The '回忆' abstraction behind the agent tools.

Part of the unified memory system (`refactor/agent-memory`). The unified design covers data model, retrieval stack and migration
(author-local; the § numbers below cite its sections). Mechanism vs policy split
(§3): the Engine holds the fixed lifecycle algorithm; each kind's Spec holds
policy. No vectors — recall is BM25 + grep + structured filters.
