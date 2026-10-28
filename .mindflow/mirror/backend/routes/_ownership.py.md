---
code_file: backend/routes/_ownership.py
stub: false
last_verified: 2026-10-28
---

## 2026-10-28（PR #419 review I-4）— `assert_visible` 搬进来

"这个 viewer 能不能看到/驱动这个 agent（自有 **或** 公开）"原来住在
`dashboard/routes.py::assert_agent_visible`，`/ws/agent/run` 从一个大路由模块 import 鉴权原语。
现在本模块是它的家：`assert_visible(viewer_id, agent_id)`，行为原样（自有或 `is_public`，
未知与外人私有统一 404，返回 agent 行）。dashboard 以旧名再导出（[[plugin_sdk_host]] 把它
发布到 `contracts.web.WebHost.agent_visible`，builtin.job 的 dashboard router 在用），
websocket 直接用 `_ownership.assert_visible`。

**与 `check_owned` / `assert_owned` 的契约差异（刻意保留，不是漏改）**：
- 入参是已解析的 `viewer_id`，不是 request → **没有 local no-op**；local 下调用方传入
  `?x_user_id=` 锚定的本地用户，照样判定。
- **不走 `_deny_reason`**：DB 失败不转 503，而是原样抛出非 HTTP 异常——WS 的
  `except HTTPException` 接不住它，落到外层变 `InternalError` 帧，dashboard 侧变 500。两者都
  不会把 DB 故障报成"agent 不存在"，这次是写下来的设计而非碰巧。若把它改成走 `_deny_reason`，
  WS 那里的 `except HTTPException` 会把 503 重写成 `AgentNotFound`，故不做。
- DB 访问保持原样：调用时从 `db_factory` 取 `get_db_client`（不用模块级名字），这样 patch
  `db_factory.get_db_client` 的既有测试继续命中。
## 2026-10-28（审后）— `_deny_reason` 接受已解析的 `user_id`

`require_owned_workspace_agent` 先 `resolve_current_user_id`（无身份 401），再把这个 user_id
显式传给 `_deny_reason(user_id=...)`：决策里的 local-mode 放行分支对它不可达，不再依赖
"两处读同一个 request.state 字段恰好一致"。两个旧 surface 不传，语义一字不改。

## 2026-10-28（PR #419 review I-7 / M-13）— 第四个 surface：`assert_visible`

合并 #419 后本文件是**四个** surface，不是三个。docstring 的 `Three surfaces` 一句已
改成 `Four surfaces`，并补上第四条。四个 surface 按「调用方拿到什么形状」区分：

| surface | 返回 / 抛出 | 典型调用方 | local 模式 |
|---|---|---|---|
| `check_owned` | error string \| None | 渠道路由（包成 `{"success": False, "error": …}`） | 跳过鉴权 |
| `assert_owned` | 抛 HTTPException 404/403 | home_assistant 式路由 | 跳过鉴权 |
| `require_owned_workspace_agent` | 返回 user_id，统一 404 | 把 agent_id 变成 workspace 目录的路由（files/attachments/skills） | **要身份，不跳过** |
| `assert_visible` | 非自有且非公开则抛 | 读 / 驱动面（websocket、chat event-log） | 无 no-op（调用方传本地 user_id） |

前三个共享 `_deny_reason` 的同一决策；`assert_visible` 走另一条问题线（能不能看/驱动，
自有 OR 公开），查库失败直接把 db 异常抛出去（非 HTTP，5xx），不经 `_deny_reason`。

## 2026-10-28 — 第三个 surface：`require_owned_workspace_agent`（跨租户读事件）

`/api/agents/../files` 把 URL 段拼进 `{base}/{user_id}/{agent_id}` 读走了 246 个用户的文件。
修复把"URL 里的 agent_id → 目录"之间缺失的确权收口到本文件，而不是再造一份副本
（本文件存在的理由就是副本会漂移）。它与前两个 surface 的差异都是刻意的：

- **身份必须存在**（`resolve_current_user_id`，无则 401），没有 local-mode 放行——保护对象
  是文件系统，不是 bot 绑定；
- 先过 [[workspace_paths]] 的 `safe_id_segment`，`..` / `%2e%2e` / `a/b` 在任何 `Path` 之前就被拒；
- unknown 与 not_owner **统一 404**：面向磁盘的路由不能成为"别人的 agent_id 存在吗"的探针；
- 查库失败仍由 `_deny_reason` 抛 503，三个 surface 共享同一决策。
- 返回 user_id，路由用被校验过的同一身份拼路径。

调用方：[[files]]、[[attachments]]（backend 直接 import），以及插件侧的 skills 路由经
[[web]] seam（`sdk.web.require_workspace_agent_owner` → [[plugin_sdk_host]]）。

## Why it exists

Canonical agent-ownership verification for backend routes. The channel
routes (slack/lark/telegram/discord/wechat/narramessenger) and
home_assistant had each pasted their own `_verify_agent_ownership` /
`_require_agent_owner` — the copies drift apart, which is exactly the bug
`AgentRepository.resolve_owner` already fixed on the agent side. This is its
backend-route counterpart, built ON that seam. Same-shape copies NOT yet
absorbed (each with drifted semantics needing its own judgement, tracked as
follow-ups): `agents/artifacts.py`, `agents/llm_config.py`,
`agents/circuit_breaker.py`, `migrate.py` (that one checks existence even in
local mode — replacing it with `assert_owned` would NOT be equivalent).

## Model

Ownership = `agents.created_by` (via `AgentRepository.resolve_owner`). ONE
internal deny-reason decision (`_deny_reason` → unknown / not_owner /
None-allow, **db failure raises 503 right there**); the two public surfaces
only map reasons to their historical shapes — neither ever parses the
other's prose (review #1: the 404-vs-403 split used to hang off an
error-string substring):
- `check_owned(request, agent_id) -> str | None` — channel routes wrap the
  string in `{"success": False, "error": ...}`.
- `assert_owned(request, agent_id)` — HTTPException(404 unknown / 403
  non-owner); home_assistant-style calls it directly now (the pass-through
  wrapper is gone).

## Decisions / gotchas

- **SECURITY POSTURE (the load-bearing warning, review #5).** In local mode
  (no `request.state.user_id`) enforcement is SKIPPED — every route behind
  this helper is then effectively unauthenticated; any HTTP caller reaching
  the backend port can bind/unbind/test any bot. Do NOT hang sensitive
  operations off this helper assuming auth exists. All IM-channel routes
  mirror this contract; keep them in lockstep.
- **Infrastructure failure ≠ "not found" (review #4, round-2 #4).**
  `resolve_owner` distinguishes `None` (lookup failed) from `""` (unknown
  agent). The 503 for a failed lookup is raised inside `_deny_reason` so it
  reaches BOTH surfaces — `check_owned`'s callers wrap returned strings in
  200 payloads, and a string there would leave a db outage with zero 5xx to
  alarm on (incident lessons #3/#5).
- **Fail-closed on unknown owner** — an unknown agent denies (404), never
  leaks. An existing row with NULL/empty `created_by` also reads as unknown
  (404 where old copies said 403 — both deny; "an agent nobody owns" is
  closer to not-found than to somebody-else's; intentional).
- Channel files keep their historical local name via a module-level alias
  (`from ... import check_owned as _verify_agent_ownership`) — no per-file
  wrapper bodies to drift (review minor #6); no import cycle because this
  module never imports back into routes.
