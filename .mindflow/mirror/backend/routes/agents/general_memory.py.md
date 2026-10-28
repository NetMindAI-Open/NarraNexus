---
code_file: backend/routes/agents/general_memory.py
last_verified: 2026-08-10
stub: false
---

## 2026-08-10 (PR-11) — grep 路由现服务 regex

删掉 `if regex: return refuse` 分支、regex 透传给 coord.grep_memory。原来拒 regex 是因 stdlib re 在共享 loop 上是 self-service DoS；现 [[retrieval]] grep_filter 用 `regex` 包+timeout，安全。路由仍保 Field 上限（pattern 1-256/limit 1-200）作直连 HTTP 调用方 backstop；seam 侧 [[store]] `_grep_reject`+clamp 两 store 镜像。 响应新增 `truncated` 键（[[coordinator]] 的截断信号透出：ReDoS 预算耗尽/单条超时=部分结果）；文件头重写（去掉过时 PR-2 引用、补两端点参数上界、说明 regex 现已服务）。


## 2026-08-10 (PR-3) — 渲染改用共享 `format_memory_hits`

路由本地 `_format` 删除,改 import [[memory/coordinator]] 的
`format_memory_hits`(唯一真源,铁律 #21 允许 backend→xyz_agent_context 的
import)。此前 route/tool/store 三份逐字拷贝靠注释维持一致,唯一分支字段
`source` 无测试覆盖。参数上界(query 1-512/limit 1-100/content ≤64KB/source
≤512)保持不变——它们是本 HTTP 面的权威守卫(不经 HttpStore 也可达),越界由
FastAPI 直接 422;HttpStore 侧镜像其可归一化子集并把 422 翻成可改参消息。

## 2026-08-10 (pre-open review) — regex 模式在 HTTP 层被拒 + 全参数上界

C1:引擎用 stdlib re 同步编译调用方 pattern,(a+)+$ 类灾难回溯实测单条
40 字符记录 >30s——发布在共享 API 进程上等于自服务 DoS(MCP 孪生跑在
per-module 进程里只伤它自己的 agent,所以保留 regex)。HTTP 端点对
regex=true 直接返回 success:false 说明文案;PR-3 把 grep 迁 HttpStore 前
必须先换 timeout 引擎(regex 包/re2),否则 parity 缺口保持记录状态。
所有 query/body 参数加 Field/Query 上界(pattern≤256,query≤512,
limit≤100/200,content≤64KB)——本家族既有惯例(me.py/feedback.py)。

# agents/general_memory.py — HTTP twin of the GeneralMemory MCP tools

## 为什么存在

The MCP data-access seam (AgentDataStore's Http path) needs an HTTP-callable
counterpart of every MCP tool that touches the database, so the mcp
container can drop its own db credentials and instead call back into the
backend over HTTP. `remember` / `grep_memory` / `memory_retain` are the
three GeneralMemory tools defined in
`_general_memory_mcp_tools.py`; this file is their route-layer twin — same
call shape (`MemoryCoordinator(MemoryEngine(db, agent_id))`), same response
dict keys, so a caller going through HttpStore gets byte-identical payloads
to the in-process MCP path. It has to be a separate file (not folded into
an existing `agents_*` route module) because it is one leg of a matched
pair with the MCP tools file — keeping it isolated makes the "does the HTTP
twin still match the MCP original" diff a single-file comparison.

## 上下游关系

- **被谁用**：`backend/routes/agents/core.py`'s aggregator
  (`router.include_router(general_memory_router)`), mounted under
  `/api/agents`. The eventual caller is AgentDataStore's HttpStore
  implementation (PR-2's stated purpose) once it's wired to route
  general-memory reads/writes here instead of a direct db connection.
- **依赖谁**：`narranexus.platform.memory` (`MemoryCoordinator`, `MemoryEngine`,
  `MemoryRecord`, `SCOPE_AGENT` — the same trio the MCP tools use),
  `narranexus.platform.utils.db.db_factory.get_db_client` (NOT
  `XYZBaseModule.get_mcp_db_client`, which is confirmed to be a thin wrapper
  around the same factory — using the factory directly avoids pulling MCP
  module machinery into a plain FastAPI route), and
  `backend.routes._ownership.assert_owned` for the ownership gate.

## 设计决策

- **Mirrors the MCP tool's try/except-and-degrade shape instead of raising
  HTTPException on underlying failure.** The MCP tools never let an
  exception escape to the LLM — they catch and return
  `{"success": False, "error": ...}` so a failed recall doesn't crash the
  agent's turn. The HTTP twin keeps that same contract (200 + success:false)
  for the *retrieval mechanics* failing, so a caller that already treats the
  MCP tool's dict shape as truth (like a future HttpStore) doesn't need a
  second failure-handling path. Ownership failures are the one exception —
  those raise via `assert_owned` (404/403/503), matching every other
  ownership-gated route in this package (home_assistant.py, etc.), not the
  channel-routes' `{"success": False}` convention.
- **`get_db_client()` from the factory, not `XYZBaseModule.get_mcp_db_client()`.**
  The MCP tools call the latter because they run inside the MCP server
  process; a backend route has no reason to route through module
  machinery for something that's confirmed to be a thin wrapper over the
  same per-event-loop factory.
- **POST body is a small `MemoryRetainBody` Pydantic model** (content,
  source) rather than query params, following the same GET-for-reads /
  POST-body-for-writes split every other `agents_*` route in this package
  uses.

## Gotcha / 边界情况

- **触发**：当调用方以为 `remember`/`grep_memory` 的空结果集是"这个 agent
  没有记忆"时 → **症状**：其实可能是底层查询失败被吞掉 → **根因**：失败路径
  返回的 shape (`{"success": False, "error": ..., "memories": []}`) 和真正
  的空结果 (`{"success": True, "memories": []}`) 都带一个空列表；调用方必须
  先看 `success` 字段，不能只看列表是否为空。这是从 MCP 原版继承的行为，不是
  这个文件引入的新坑，但 HTTP 调用方（尤其是前端）容易漏掉。
- **触发**：当你以为 `assert_owned` 会像其它 general-memory 字段一样返回
  `{"success": False}` → **症状**：实际收到的是一个 HTTP 404/403/503，body
  是 FastAPI 默认的 `{"detail": ...}"` shape，不是这个文件里其它分支用的
  `{"success": False, "error": ...}` shape → **根因**：`assert_owned` 是
  home_assistant.py 那一路 raise-HTTPException 的契约，和 `check_owned`
  （channel 路由用的、返回错误字符串再包 200 的那一路）是两套不同的
  ownership 呈现方式（见 `_ownership.py` 的模块 docstring）；这个文件选的是
  raise 那一路。

## 新人易踩的坑

- Local mode（没有 `request.state.user_id`，即没有走鉴权中间件）下
  `assert_owned` 直接放行——任何调用方都能读写任何 agent 的记忆。这不是本文件
  的 bug，是 `_ownership.py` 记录在案的安全姿态；在这个 helper 后面挂敏感操作
  之前必须先确认部署环境真的启用了身份中间件。
- 新增第四个 GeneralMemory MCP 工具时，如果不同步给这个文件加对应路由，
  HttpStore 路径会比 MCP 路径少一个能力——这一对文件目前没有自动化的
  "工具清单一致性" 检查，全靠人工在改 `_general_memory_mcp_tools.py` 时
  记得回头看这里。

## 相关约束

- 铁律 #10（Tier-2 doc sync）—— 本文件伴随 `general_memory.py` 的实现同一
  commit 落地。
- 见 `.mindflow/mirror/backend/routes/_ownership.py.md`（若存在）或直接读
  `_ownership.py` 的模块 docstring —— ownership 双呈现契约（`check_owned` vs
  `assert_owned`）在那里定义，本文件只是它的又一个使用方。
