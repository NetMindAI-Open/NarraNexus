---
code_file: src/narranexus/platform/memory/engine.py
last_verified: 2026-08-10
stub: false
---

## 2026-08-10 (PR-11) — grep 加 offload + deadline，返回 (hits, truncated)

`grep` 的 regex 路径用 `run_in_executor` 把 CPU-bound 的 [[retrieval]] grep_filter 丢出事件循环（`regex` 匹配释放 GIL，真 offload），避免单进程 uvicorn 上阻塞共享 loop；子串路径便宜、仍 inline。加 `deadline` 参（[[coordinator]] 传的 per-request 预算）、返回 `(hits, truncated)` 透出截断信号。

**用专用有界池 `_GREP_EXECUTOR`（max_workers=2）而非默认共享池**（管线 Important）：regex 匹配释放 GIL，若用无上限的默认池，N 个并发 regex grep 会吃满所有核 + 霸占 office_watch 等共用的默认池——封顶 2 线程后超额请求在 await 上排队，最坏是 grep 变慢而非全局降速。**勿改回 None**。


## 2026-07-03 — explicit db to the dedup tie-break LLM call (Phase 0 / module H)

`_llm_tiebreak` and `consolidate()` now pass `db=self._db` to the helper SDK so
token usage is recorded even with no ambient cost context — double insurance on
top of the caller's `set_cost_context` (the consolidation worker). Turn-time
dedup was already covered by the runtime's cost context; this makes the memory
paths self-sufficient. See [[cost_tracker]] and the audit checklist.

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

Part of the unified memory system (`refactor/agent-memory`). The unified design covers data model, retrieval stack and migration
(author-local; the § numbers below cite its sections). Mechanism vs policy split
(§3): the Engine holds the fixed lifecycle algorithm; each kind's Spec holds
policy. No vectors — recall is BM25 + grep + structured filters.
