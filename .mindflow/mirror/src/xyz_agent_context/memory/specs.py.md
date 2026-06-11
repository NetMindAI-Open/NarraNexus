---
code_file: src/xyz_agent_context/memory/specs.py
last_verified: 2026-06-08
stub: false
---

## 2026-06-08 — passive flags + `chat` kind retired

observation / entity / narrative are marked `passive=True` (auto-injected distilled knowledge). The `chat` kind is RETIRED: conversation search is now the per-interaction `event` index (chat+event merged — one index per turn carrying user input + final output). The `memory_chat` table is kept (migration still references it) but is no longer a registered searchable kind. event / bus / job are registered as searchable-but-not-passive. See [[spec]] for the passive mechanism.

# specs.py — unified Agent Memory

Registers all 7 kinds (event/chat/bus/narrative/entity/job/observation) with their policies. Imported for side-effect at package import so kinds are always available.

Part of the unified memory system (`refactor/agent-memory`). Full design,
data model, retrieval stack and migration: reference/self_notebook/specs/2026-06-03-agent-memory-unification-design.md. Mechanism vs policy split
(§3): the Engine holds the fixed lifecycle algorithm; each kind's Spec holds
policy. No vectors — recall is BM25 + grep + structured filters.
