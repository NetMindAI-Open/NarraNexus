---
code_file: plugins/builtin.home_assistant/src/narranexus_plugins/home_assistant_module/routes.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28 — `/test` `/verify` 不再回显异常文本

两个端点以前 `except Exception as e: return {"ok": False, "error": str(e)}`——任意异常文本
（可能带内网地址 / 上游细节）直接回给用户。现在只有 `HAError` 的文本原样返回（那是
[[ha_client]] 自己的词表：状态码 / 固定措辞，不含上游内容），其余异常回固定
`_GENERIC_FAILURE`，细节进日志。`/verify` 补了显式 `except HAError` 分支，两端点行为一致。
云端内网 URL 由 ha_client 每次请求前的共享出网门拒掉（请求根本不发）。

## 2026-09-07 — 宿主依赖改走 `narranexus.sdk.web`（批 6c，G2-I1）

批 6b 把 router 搬进插件包时，`from backend.*` 没跟着走：本文件当时还在 import 宿主的
私有模块（`routes._ownership` / `routes._mcp_egress` / `routes.dashboard.routes` 的下划线
函数）或非契约的公开符号（`backend.auth` / `backend.auth_errors` / `backend.config`）。
现在全部改成 `narranexus.sdk.web` 的 seam（实现见 [[plugin_sdk_host]]，形状见
[[web]]）。这不是改 import 路径的洁癖——第三方照抄这个 router 时，
`assert_owned` / `resolve_current_user_id` 一个都拿不到，只剩「自己重写鉴权」
（2026-08-12 那批 IDOR 的来源）这一条路。`pyproject.toml` 的
`plugins never import the host (backend)` 契约把这条线钉死，豁免列表为空。

## 2026-08-11 (PR-G) — /verify 的 resolve_client 去 db 参数

`resolve_client(db, agent_id)` → `resolve_client(agent_id)`（[[binding]] 现自取 seam；backend 进程内 seam=DirectStore→本地 db）。db 仍用于 _require_agent_owner。


# home_assistant.py — HA 绑定的后端路由(配置页用)

## 为什么存在

前端"绑定 Home Assistant"配置页的后端:查/存绑定 + 测连。session 鉴权。

## 端点

- `GET /api/home-assistant/binding?agent_id=` — 返回当前绑定,**token 掩码**(只显示末 4 位),前端能显示
  "已配置"而不泄密;未绑定返回 `{bound: False}`。
- `PUT /api/home-assistant/binding` `{agent_id,base_url,token,verify_tls}` — 存/替换绑定。
- `POST /api/home-assistant/test` `{base_url,token,verify_tls}` — 用 HAClient ping + 数实体,返回
  `{ok, entity_count}` 或 `{ok:False,error}`(测**表单里现填的** URL,绝不 500)。
- `POST /api/home-assistant/verify` `{agent_id}` — 走 `resolve_client` 读**已存的**绑定再 ping,
  返回同款 `{ok, entity_count}`/`{ok:False,error}`。和 MCP 工具同一条读取路径,绿了即证明 agent 真能连上
  (不像 `/test` 只验表单值);token 不回传。

## 关键点

- **鉴权 ≠ 授权**:`resolve_current_user_id` 只解决"你是谁";`agent_id` 是调用方给的,所以带 agent_id 的
  端点(GET/PUT binding、`/verify`)必须再过 `assert_owned`(canonical helper `backend/routes/_ownership.py`,
  查 `agents.created_by == 当前 user_id`),否则云端任意登录用户可读/改他人 agent 绑定、用受害者 token
  打对方家里(跨租户 IDOR)。本地模式(无 JWT)不强制。`/test` 无 agent_id,靠 ha_client 的云端 SSRF 护栏。
- 绑定键在 `agent_id` 上(照 Lark 凭据模型),不依赖 module instance——配置面板在 agent 尚未跑过
  任何 turn 时就能查看/保存(instance 是懒创建的,曾导致 GET binding 400)。
- `/test` vs `/verify` 是刻意区分:前者验用户**现填**的值(还没存),后者验**已存**的绑定(agent 实际会用的)。
- HAClient / resolve_client 在各自端点内延迟 import(避免路由 import 期拉入模块代码)。

## 上下游

- **注册**:`backend/main.py` `include_router(prefix="/api/home-assistant")`。
- **依赖**:`repository.HomeAssistantBindingRepository`、`schema.HAConfig`、`_home_assistant_impl.ha_client`
  (`/test`)、`_home_assistant_impl.binding.resolve_client`(`/verify`)。

## 2026-09-04 · plugin-owned router (batch 3c.5)

`ROUTES` — `builtin.home_assistant`'s `/api/home-assistant` router (mounted by `backend/plugins_host`, not by main.py).

## 2026-09-07 — moved into the plugin package

This router is the plugin's own contribution (`narranexus_plugins.home_assistant_module.routes:ROUTES`), not a file under `backend/routes/` that the plugin's manifest reached out to: the package is self-contained (a distribution that leaves the plugin out has no dead router in the wheel), and the backend no longer imports the plugin's private modules to serve it. Builtin routers keep their absolute prefixes (`/api/...`); third-party plugins live under `/api/x/<id>` — the one deliberate difference, recorded in docs/API_POLICY.md.
