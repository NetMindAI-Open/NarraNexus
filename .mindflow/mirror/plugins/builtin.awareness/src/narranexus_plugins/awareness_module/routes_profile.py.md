---
code_file: plugins/builtin.awareness/src/narranexus_plugins/awareness_module/routes_profile.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — 宿主依赖改走 `narranexus.sdk.web`（批 6c，G2-I1）

批 6b 把 router 搬进插件包时，`from backend.*` 没跟着走：本文件当时还在 import 宿主的
私有模块（`routes._ownership` / `routes._mcp_egress` / `routes.dashboard.routes` 的下划线
函数）或非契约的公开符号（`backend.auth` / `backend.auth_errors` / `backend.config`）。
现在全部改成 `narranexus.sdk.web` 的 seam（实现见 [[plugin_sdk_host]]，形状见
[[web]]）。这不是改 import 路径的洁癖——第三方照抄这个 router 时，
`assert_owned` / `resolve_current_user_id` 一个都拿不到，只剩「自己重写鉴权」
（2026-08-12 那批 IDOR 的来源）这一条路。`pyproject.toml` 的
`plugins never import the host (backend)` 契约把这条线钉死，豁免列表为空。

# agents/profile.py — update_agent_profile 的 seam 孪生路由（agent-scoped, owner-gated）

## 为什么存在（PR-9）

`POST /api/agents/{agent_id}/profile/update` 是 update_agent_profile 工具的 byte-parity Http
孪生，让 HttpStore 无需 db 凭据也能跑改名事务。与 seam 的 DirectStore 调**同一个**
[[_awareness_writes]] `update_agent_profile_from_args`。owner-gated（assert_owned）。

## 消息信封（区别于 awareness 路由）

工具返回**动态**状态串（改了哪些字段 + 重名 note），不是 awareness 那种定值常量——所以本端点
不走「结构化响应 + HttpStore 重建常量」那套，而是把工具串**原样**放进 `{"message": <str>}`
信封，[[store]] HttpStore 拆信封原样返回。handler 对已处理结果恒 200；get_db_client 失败兜成
`{"message": "Error: ..."}`。挂载在 [[core]]。

## 长度上限故意不放路由 body

`ProfileUpdateBody` **不加** `Field(max_length=AGENT_TEXT_MAX_LENGTH)`：上限在共享 [[_awareness_writes]] `update_agent_profile_from_args` 里 enforce、返回可读的 "Error: … too long" 串(HTTP 200)。若在路由 body 加 Field 会 422 抢在 fn 前 → HttpStore 收 "rejected (422)" 而 DirectStore 返 fn 串 → 破 byte-parity。单点 enforce 两条路径逐字节同，过长值仍被拒(绝不落库)。

## 2026-09-04 · data-access providers (batch 3c.4)

`ROUTES` — this router is the `backend.routes` contribution of builtin.awareness (prefix `/api/agents`, mounted by `backend/plugins_host`), no longer included by `routes/agents/core.py`; disabling the plugin makes these paths 404 together with the MCP-side provider.

## 2026-09-07 — moved into the plugin package

This router is the plugin's own contribution (`narranexus_plugins.awareness_module.routes_profile:ROUTES`), not a file under `backend/routes/` that the plugin's manifest reached out to: the package is self-contained (a distribution that leaves the plugin out has no dead router in the wheel), and the backend no longer imports the plugin's private modules to serve it. Builtin routers keep their absolute prefixes (`/api/...`); third-party plugins live under `/api/x/<id>` — the one deliberate difference, recorded in docs/API_POLICY.md.
