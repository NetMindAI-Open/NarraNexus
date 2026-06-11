---
code_file: src/xyz_agent_context/narrative/_narrative_impl/crud.py
last_verified: 2026-06-08
stub: false
---

# crud.py — Narrative CRUD (+ search-index projection)

## Why it exists

`NarrativeCRUD` is the private create / load / save / query implementation behind `NarrativeService` for the `narratives` operational table. `create()` provisions a new narrative (default user+agent actors and a ChatModule chat instance via `InstanceFactory`, ensuring agent-level instances exist). `save()` / `upsert()` persist it; `load_by_id` / `load_by_agent_user` read it back.

## 2026-06-08 — narrative projected into the unified search index

`save()` and `upsert()` now call `_index_narrative()` after persisting. It projects the narrative's searchable surface (name + current_summary + description + topic_keywords) into `memory_narrative` via `MemoryEngine.index('narrative', id, …)`, with a `source_ref` pointer back to the narrative. This is the SINGLE write point — `create()` flows through `save()` too — so narratives stay findable via `remember` and never go stale. The projected fields are deliberately the SAME ones narrative ROUTING uses (`retrieval.py`), and both share `bm25_rank`, so turn-routing and `remember` rank narratives on identical text. Best-effort: an index failure never breaks narrative persistence.

## Upstream / Downstream

`NarrativeService` → `NarrativeCRUD` → `NarrativeRepository` (operational `narratives` table) + `MemoryEngine` (search projection). `InstanceFactory` for chat-instance provisioning on `create()`.

## Gotchas

- The narrative operational row is the source of truth; `memory_narrative` is a read-only search projection. Deleting a narrative row does NOT currently cascade-delete its index row (known gap — orphan index pointer; tracked in TODO-unified-memory-overhaul.md).
