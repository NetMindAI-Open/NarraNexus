---
code_file: plugins/builtin.job/src/narranexus_plugins/job_module/routes_agent.py
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

## 2026-08-11 — job 三写全孪生：POST /{agent_id}/jobs（create）、PUT .../{job_id}/pause|cancel

调共享 [[_job_writes]] `create_job_from_args`/`pause_job_from_args`/`cancel_job_from_args`
（与 DirectStore 同源、byte-parity）。owner-gated，各包 try 兜 `get_db_client()`。
**job_create 的 owner LLM-context 设置 + 相似标题 embedding dedup 在本 backend 侧跑**——
云端 DB（及 owner LLM 配置）在这里，mcp 容器无凭据加载不了。`JobCreateSeamBody` 同
`JobUpdateSeamBody` 一样 `extra="forbid"`（漏改字段 422 响亮失败，杜绝 HttpStore 静默丢字段）。
pause/cancel 是 bodyless PUT（job_id 全在路径），HttpStore 侧走新增的 `_put_dict`。

## 2026-08-10 (PR-8b) — 新增 job_update 孪生端点 POST /{agent_id}/jobs/{job_id}/update

调共享 [[_job_writes]] `update_job_from_args`（与 DirectStore、前端 jobs PUT 同源）。
owner-gated；包 try 兜 get_db_client() → 200+{success:False,job_id,message}。

**PR-8b r2（管线 fix-first 修）**：`JobUpdateSeamBody` 不再手抄 9 字段，改 derive 自
[[job_schema]] 的共享 `JobUpdateFields`（字段清单单点声明，与前端 `JobUpdateBody` 同源），
并加 `model_config=ConfigDict(extra="forbid")`。forbid 是承重的：这是 HttpStore 写路径，
将来给 `update_job_from_args`+工具加字段却漏改本 body 时，必须以 **422 响亮失败**（HttpStore
`_parse_dict` 翻成 "invalid arguments"），而不是 pydantic 默认 `ignore` 静默丢字段——
静默丢=云上不写、本地 DirectStore 却写=正是 seam byte-parity 要防的本地/云分叉。


# agents/jobs.py — job 读+写 seam 孪生端点（agent-scoped, owner-gated）

## 为什么存在（PR-8）

JobModule 三个读工具的 byte-parity Http 孪生，让 HttpStore 无需 db 凭据。每个端点
调 [[_job_reads]] 的共享 fetch/search（与 seam 的 DirectStore 同一函数）→ 两路逐字
相同。owner-gated（`assert_owned`），挂 `/api/agents/{agent_id}/jobs`。

**与 `backend/routes/jobs.py` 区分**：那是前端向 job API（`/jobs` 前缀、response_model
形状、无 owner gate）；本文件是 seam 孪生。搜索用 POST（keyword 列表/过滤走 body），
by-id 用 GET。三个端点整体包 try 兜 `get_db_client()` 获取失败 → 200+{success:False,error}，
与 DirectStore 对齐。limit `Field(le=100)`，HttpStore 侧 `_clamp_limit` 预夹取避免 422。

**user_id 归属决定（预审 Important）**：搜索端点**故意接受**调用方传的 optional user_id（默认 None=该 agent 下全部用户 job），因为其调用方是 agent 本身（经身份转发 seam）查自己 agent 的 job，逐字保留工具签名以维持 Direct/Http parity。与 `backend/routes/jobs.py` 相反（那里 user_id 强制为登录用户，因为它是用户浏览器面，一个用户不能读另一个用户的 job）。两者对各自 actor 都对；assert_owned 把这些端点门控到 agent owner。

## 2026-09-04 · data-access providers (batch 3c.4)

`ROUTES` — this router is the `backend.routes` contribution of builtin.job (prefix `/api/agents`, mounted by `backend/plugins_host`), no longer included by `routes/agents/core.py`; disabling the plugin makes these paths 404 together with the MCP-side provider.

## 2026-09-07 — moved into the plugin package

This router is the plugin's own contribution (`narranexus_plugins.job_module.routes_agent:ROUTES`), not a file under `backend/routes/` that the plugin's manifest reached out to: the package is self-contained (a distribution that leaves the plugin out has no dead router in the wheel), and the backend no longer imports the plugin's private modules to serve it. Builtin routers keep their absolute prefixes (`/api/...`); third-party plugins live under `/api/x/<id>` — the one deliberate difference, recorded in docs/API_POLICY.md.
