---
code_file: src/xyz_agent_context/services/netmind_key_client.py
last_verified: 2026-07-02
stub: false
---

# netmind_key_client.py — NetMind Key 管理 API 客户端（生成推理 key）

## 为什么存在

模块 F（Phase 5）的"一键使用此订阅"需要**代用户生成一个 NetMind 推理 API key**，
才能把它接到 agent/helper 槽、无需用户手动贴 key。这个 client 只干这件事。

## 和 [[netmind_billing_client]] 的区别（别混）

- **不同 host**：`platform-api.netmind.ai`（prod）/ `mind-web.protago-dev.com`（dev），
  由 `settings.netmind_key_api_base` 配置。
- **不同鉴权头**：`token: Bearer <jwt>`（**不是** `loginToken`）——但**同一个 JWT**
  （dev 2026-07-02 实测：登录 JWT 两个域都认，只是头名不同）。
- **表单编码**（`application/x-www-form-urlencoded`）。
- **信封陷阱**：错误是 **HTTP 200 + `{"success": false, "errorcode": ...}`**，光看状态码
  不可靠，必须解 body。`NOT_LOGGEDIN` = token 无效。

## 关键设计

- `create_and_get_token(jwt, name, currency)`：addApiToken **不返回 key 字符串**，所以
  **create-then-list**——先建命名 key，再 queryApitokenList 按 name 取 createTime 最新的
  那条的 apitoken 返回。
- 两值错误：`KeyAuthError`（NOT_LOGGEDIN，caller→401）/ `KeyUpstreamError`（网络/5xx/
  非鉴权失败/畸形，caller→502）。
- 可注入 `transport` 供单测。**绝不 log jwt 或生成的 apitoken。**

## 上下游

- 上游：`POST /api/providers/use-subscription`（[[providers]]）唯一调用方。
- 下游：NetMind Key 管理域。
- 生成的 key 交给 `UserProviderService.onboard_one_key(uid, key, "netmind")` 建 provider
  + 绑槽（复用，不新写）。

## 门禁

`use-subscription` 由 `settings.netmind_use_subscription_enabled` 开关控制，**默认关**，
待 **C1**（key 消耗是否按订阅→余额计费、体现在 user-fee-info）与 NetMind 确认后再开。

## 审查加固（2026-07-02）

- **唯一 key 名**：`create_key` 用 `NarraNexus-<uuid8>` 每次唯一，并按该名精确匹配
  取回——根治"账号已有同名 key 时选错 key"（质量/安全 HIGH），name 过滤也让分页问题消失。
- **返回 `MintedKey(apitoken, token_id)`**：带回 id 供失败时撤销。
- **`delete_key`**：best-effort 撤销（never raises），用于孤儿清理。
- **map 非 dict 守卫**：`isinstance` 判断，畸形 map 不再 AttributeError→500。
