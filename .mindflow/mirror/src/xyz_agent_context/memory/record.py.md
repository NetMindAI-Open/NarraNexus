---
code_file: src/xyz_agent_context/memory/record.py
last_verified: 2026-06-08
stub: false
---

## 2026-06-08 — `source_ref`: pointer back to the operational source

Added `source_ref: Optional[Dict[str,str]] = None` ({"kind","id"}). It distinguishes the two record archetypes: self-contained kinds (observation/entity) leave it None — the row IS the data; projection kinds (narrative/interaction/job/bus) set it so `remember` can hand the agent a pointer to fetch the live original via the matching by-id tool. Semantically distinct from `source_ids` (provenance — which events produced this); both coexist. Additive column only (iron rule #6).

# record.py — unified Agent Memory

The single MemoryRecord shape every kind shares (content_text surface + attributes payload + bi-temporal valid/invalid/created/expired + provenance + history). Row (de)serialization tolerant to legacy timestamps.

Part of the unified memory system (`refactor/agent-memory`). The unified design covers data model, retrieval stack and migration
(author-local; the § numbers below cite its sections). Mechanism vs policy split
(§3): the Engine holds the fixed lifecycle algorithm; each kind's Spec holds
policy. No vectors — recall is BM25 + grep + structured filters.
