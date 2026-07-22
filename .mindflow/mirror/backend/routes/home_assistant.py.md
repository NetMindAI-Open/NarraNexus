---
code_file: backend/routes/home_assistant.py
last_verified: 2026-07-17
stub: false
---

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
  端点(GET/PUT binding、`/verify`)必须再过 `_require_agent_owner`(仿 `lark.py::_verify_agent_ownership`,
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
