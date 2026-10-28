---
code_file: src/narranexus/platform/memory/coordinator.py
last_verified: 2026-08-10
stub: false
---

## 2026-08-10 (PR-11) — grep_memory 每请求一份预算 + 返回 (hits, truncated)

`grep_memory` 现算**一次** deadline(`_GREP_REQUEST_BUDGET_S`) 逐 kind 传给 [[engine]] grep——全请求跨 ~6 kind 共享一份 CPU 预算，而非 budget×num_kinds（管线 Critical：单进程共享 loop 上可被反复触发的可用性 DoS）。返回 `(hits, truncated)`：任一 kind 截断或预算耗尽（跳过剩余 kind）即 truncated=True，透给调用方别把部分结果当完整。


## 2026-08-10 (PR-3) — `format_memory_hits` 落户此处，成为唯一渲染器

新增模块级 `format_memory_hits(hits) -> list[dict]`（就在 `MemoryHit` 旁，
渲染的正是它），从 memory 包导出。这是「把召回的记忆返回给 agent」的**唯一**
渲染实现:`remember`/`grep_memory` MCP 工具、AgentDataStore 的 DirectStore、
以及 backend `/memory/*` 路由（HttpStore 路径）全部 import 它。此前是三份逐字
拷贝靠注释维持 lockstep，其唯一分支字段 `source` 无测试覆盖——收成一份后
lockstep 由「约定」变「编译期事实」。分支字段由 [[data_access/store]] 的
parity 测试覆盖。

# coordinator.py — unified Agent Memory

MemoryCoordinator facade — cross-kind remember() (RRF-fused ranked recall) and grep_memory() (exact/regex). The '回忆' abstraction behind the agent tools.

Part of the unified memory system (`refactor/agent-memory`). The unified design covers data model, retrieval stack and migration
(author-local; the § numbers below cite its sections). Mechanism vs policy split
(§3): the Engine holds the fixed lifecycle algorithm; each kind's Spec holds
policy. No vectors — recall is BM25 + grep + structured filters.
