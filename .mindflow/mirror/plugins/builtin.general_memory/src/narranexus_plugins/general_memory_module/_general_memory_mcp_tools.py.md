---
code_file: plugins/builtin.general_memory/src/narranexus_plugins/general_memory_module/_general_memory_mcp_tools.py
last_verified: 2026-09-04
stub: false
---

## 2026-08-10 (PR-11) — grep_memory 迁 seam（memory 三工具收尾）

grep_memory 改 `get_agent_data_store().grep_memory(agent_id, pattern, regex, limit)`（原来直连 MemoryCoordinator，是唯一没上 seam 的、因等 ReDoS-safe 引擎）。engine 已在 [[retrieval]] grep_filter 换 `regex` 包+timeout。 工具 description 补一句 `truncated=true` 的含义（预算耗尽=结果可能不全，收窄 pattern 重试），因为 LLM 是这个字段的唯一读者。随迁删死 import（MemoryCoordinator/MemoryEngine/format_memory_hits/XYZBaseModule 三工具全 delegate 后无 code 引用）。**三工具全走 seam → mcp 容器可弃 db 凭据**。


## 2026-08-10 (PR-3) — remember / memory_retain 走 AgentDataStore seam

两个工具改为 `get_agent_data_store().remember/memory_retain`,数据访问移到
[[data_access/store]]:本地 DirectStore 复刻原 MemoryCoordinator/MemoryEngine
调用(行为不变),云端 HttpStore 调 backend 路由(mcp 零 db 凭据)。`grep_memory`
**仍直连**——HTTP 侧因 ReDoS 拒 regex,严格 parity 待 timeout-safe 引擎(todo)。
本文件不再有本地 `_format`:渲染统一 import [[coordinator]] 的
`format_memory_hits`(唯一真源),grep_memory 也用它。文件头 `@description`
已补 `memory_retain` 并说明前两者走 seam。


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

## 2026-09-04 · no per-module port (batch 5a)

The MCP server URL comes from `mcp_server_url("<server_name>")` (the single MCP host + `/mcp/<server_name>/sse`); the module-level port constant / `self.port` and the factory's `port` parameter are gone.
