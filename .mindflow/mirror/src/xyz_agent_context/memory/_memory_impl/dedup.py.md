---
code_file: src/xyz_agent_context/memory/_memory_impl/dedup.py
last_verified: 2026-06-03
stub: false
---

# dedup.py — unified Agent Memory

Deterministic dedup funnel (exact-normalize → Jaccard → LLM tie-break) + bi-temporal supersession arbitration. Non-vector fix for the Bob/Robert regression.

Part of the unified memory system (`refactor/agent-memory`). Full design,
data model, retrieval stack and migration: reference/self_notebook/specs/2026-06-03-agent-memory-unification-design.md. Mechanism vs policy split
(§3): the Engine holds the fixed lifecycle algorithm; each kind's Spec holds
policy. No vectors — recall is BM25 + grep + structured filters.
