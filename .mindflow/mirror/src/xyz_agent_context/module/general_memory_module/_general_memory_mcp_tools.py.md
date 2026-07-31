---
code_file: src/xyz_agent_context/module/general_memory_module/_general_memory_mcp_tools.py
last_verified: 2026-07-21
stub: false
---

## 2026-07-21 — added `memory_retain` (explicit write)

Previously this server had only READ tools (`remember`/`grep_memory`); writes
happened only implicitly via the post-turn extraction hook. That left no way for
an agent to push a KNOWN fact — needed by Agent Migration (import MEMORY.md
facts) and any "remember this verbatim" ask. `memory_retain(agent_id, content,
source="")` writes one `observation`/`world` MemoryRecord (SCOPE_AGENT, dedup by
meaning, tags `["imported"]` + `source_ref={"kind":"import","id":source}` when a
source is given). Thin wrapper over `MemoryEngine.retain`, same pattern as the
read tools.

## 2026-06-08 — remember exposes the source_ref pointer

`_format` now surfaces `item['source'] = source_ref` for projection-kind hits, teaching the agent the two-step Search→Fetch: a hit on job/event/narrative/bus carries {kind,id} to fetch the live original (job_retrieval_by_id / view_event / view_narrative). Self-contained kinds (observation/entity) omit it — the snippet is the whole thing.

# _general_memory_mcp_tools.py — unified Agent Memory

The agent-facing remember / grep_memory MCP tools (port 7809) over MemoryCoordinator — the unified recall surface replacing per-module recall tools.

Part of the unified memory system (`refactor/agent-memory`). The unified design covers data model, retrieval stack and migration
(author-local; the § numbers below cite its sections). Mechanism vs policy split
(§3): the Engine holds the fixed lifecycle algorithm; each kind's Spec holds
policy. No vectors — recall is BM25 + grep + structured filters.
