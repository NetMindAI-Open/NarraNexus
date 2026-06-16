---
code_file: src/xyz_agent_context/memory/_memory_impl/consolidate.py
last_verified: 2026-06-11
stub: false
---

# consolidate.py — unified Agent Memory

LLM consolidation (9 processing rules, Hindsight-derived) distilling raw units into evolving observations, with adaptive-bisect resilience. Fully LLM+SQL, no vectors.

Part of the unified memory system (`refactor/agent-memory`). Full design,
data model, retrieval stack and migration: reference/self_notebook/specs/2026-06-03-agent-memory-unification-design.md. Mechanism vs policy split
(§3): the Engine holds the fixed lifecycle algorithm; each kind's Spec holds
policy. No vectors — recall is BM25 + grep + structured filters.

## 2026-06-11 — systemic errors raise; only content errors bisect

P0 regression fix: the bisect-and-drop resilience treated ALL exceptions
as content errors, so the cloud worker's credential-less 401s bisected
to single facts and permanently dropped 4599 of them. New
`_is_systemic_llm_error` (auth / permission / rate-limit / connection /
5xx, walking the __cause__ chain because the Agents SDK wraps client
errors) → `SystemicLLMError` raises out of consolidate so the worker
isolates the scope with facts intact. Content/parse failures keep the
bisect-to-isolation policy unchanged.

