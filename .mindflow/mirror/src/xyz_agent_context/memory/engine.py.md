---
code_file: src/xyz_agent_context/memory/engine.py
last_verified: 2026-06-17
stub: false
---

## 2026-06-17 — helper SDK 不再硬绑 OpenAIAgentsSDK

`MemoryEngine` 默认 helper SDK 从 `OpenAIAgentsSDK()` 改为
`get_helper_sdk()`（`sdk` 参数类型从 `Optional[OpenAIAgentsSDK]` 放宽为
`Optional[Any]`）。意图是落实铁律 #9：helper_llm 调用点不再 import 具体 SDK 类，
由 factory 按当前 asyncio task 的 helper 配置（anthropic ContextVar 是否设置）
分派 Anthropic 或 OpenAI 实现，从而一把 Claude key 能同时服务 agent 槽和
helper 槽。两个 SDK 接口/返回形状一致，调用方 dispatch-blind。机制无变化。

## 2026-06-08 — `index()`: the unified projection-write entry point

Added `index(kind, source_id, text, *, scope_type, scope_id, subtype, tags, agent_id)`: the single idempotent way every PROJECTION kind (narrative / interaction / job / bus) makes its source data searchable. It writes ONE index row = searchable `content_text` + a `source_ref` {kind,id} pointer back to the operational source. `record_id` is deterministic (`idx_{kind}_{sha1(source_id)[:20]}`), so re-indexing a changed source (e.g. a job whose title changed) upserts the same row — safe to call from a per-turn hook. The engine still owns the fixed lifecycle (retain/recall/grep/consolidate/evict); `index()` is the write path for REFERENCE kinds, whereas `retain()` is for SELF-CONTAINED kinds (observation/entity). See [[record]] for `source_ref`.

# engine.py — unified Agent Memory

MemoryEngine — the 7 fixed lifecycle methods (retain/resolve/persist/consolidate/evict/recall/grep), the 'mechanism' half. Per-turn recall bounded by candidate_cap so high-volume kinds never whole-table scan.

Part of the unified memory system (`refactor/agent-memory`). Full design,
data model, retrieval stack and migration: reference/self_notebook/specs/2026-06-03-agent-memory-unification-design.md. Mechanism vs policy split
(§3): the Engine holds the fixed lifecycle algorithm; each kind's Spec holds
policy. No vectors — recall is BM25 + grep + structured filters.
