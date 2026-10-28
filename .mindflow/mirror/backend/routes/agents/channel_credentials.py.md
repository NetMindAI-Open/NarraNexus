---
code_file: backend/routes/agents/channel_credentials.py
stub: false
last_verified: 2026-08-11
---
## 2026-08-11 (lark 原语) — PATCH/PUT/DELETE credential 端点 + _gate

加 `PATCH`(部分更新深合并)/`PUT`(整条 upsert)/`DELETE`(解绑) 到 `/api/agents/{id}/channels/{ch}/credential`——原始密钥资源的**写侧**，同 GET 的 service+owner 双门(抽成 `_gate` DRY，2026-08-11 审查后 GET credential 也复用 `_gate`，四端点门禁单点定义)。全委托 seam DirectStore→manager 的 apply_patch/save_raw/delete，深合并只有一份实现。非 lark channel 命中写原语时 DirectStore 给清晰 ValueError（而非裸 AttributeError→500）。

## 2026-08-11 (审查收口) — service 门改用已验签标志 + /owner + 全 7 channel

审查指出：service 门原用 `_is_nx_service_bearer`(**未验签的 header 前缀**)——local X-User-Id 等未验签路径也可能带 `nx-agent:` 前缀。改用中间件 [[auth]] 仅在**验签成功后**才设的 `request.state.nx_service_authed`(fail-closed)，才真正证明是 executor→mcp 服务身份。三端点(credential/name/owner)全 service+owner 双门，`SUPPORTED_CHANNELS`(注册表派生，全 7 channel)先验再查。补 /name+/owner 路由测试。


## 2026-08-11 (PR-A) — 原始 channel 凭据的 HTTP 孪生端点（blueprint P2）

[[channel_store]] 的 HttpStore 打的后端端点，挂在 `/api/agents`。全库**唯一**返回原始 channel 密钥的路由，
故上**两道**门，缺一不可：

1. **service-caller 门** `_require_service_caller`：只接受 nx-agent（nx-service）bearer（复用 [[auth]] `_is_nx_service_bearer`
   查前缀；真正验签是 auth_middleware 干的、已 fail-closed）。普通用户 session JWT 即便 own 该 agent 也拒（403）——
   面板故意 mask 凭据，若允许浏览器 session 经 API 把原始 token 拉回来，等于把那层重新敞开（XSS 可批量外泄 bot token）。
2. **owner 门** [[_ownership]] `assert_owned`：token 证明的 user_id 必须 own 该 agent（挡跨租户窃取）。

`channel` 走 [[channel_store]] 的 `SUPPORTED_CHANNELS`（注册表派生）→ 未知 channel 在任何查询前先 404，
不能拿来探测未接线的 channel。命中则**委托 seam 的 `ChannelDirectStore().get_credential(channel, agent_id)`**
（不再硬编码某个 manager）——HTTP 孪生与进程内路径共用**同一份** db 访问，加 channel 只在 channel_store 注册表加一行、
本文件零改。None→`{"bound": false}`，否则透传 raw dict。**绝不 log 密钥**（raw 只在 `to_raw_dict` 一处离开管理器）。
附带 `GET /{agent_id}/channels/name`（委托 get_agent_name）+ `GET /{agent_id}/channels/owner`（委托 get_agent_owner，返回 created_by，narra 用），均同两道门。
