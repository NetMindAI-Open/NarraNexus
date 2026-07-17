---
code_file: backend/routes/billing.py
last_verified: 2026-07-13
stub: false
---

## 2026-07-13 — 门禁从"部署模式"改挂"power 轴"（本地双模式登录）

计费不再按 cloud/local 部署模式开关，改按 **power 轴**（见
[[deployment_mode]] 的两条正交轴）。`/plans` 公共目录挂
`is_power_login_enabled()`（cloud OR 本地开启 Power 登录）；每个用户维度端点
（subscription/fee-info/records/subscribe/cancel/reactivate/recharge/recharge_status）
挂新守卫 `_require_power_account(request)` —— 先 `resolve_current_user_id`
（未登录→401），再放行条件 **`is_cloud_mode()` OR `is_power_account(uid)`**：云端
短路保留改前语义（任何已登录用户可达,非 NetMind 用户后续因缺 X-Netmind-Token
仍 401,不新泄漏），本地则要求 Power 账号（[[power_account]]，非 individual→404）。
**云端短路是 review 反馈后加的**:若云端纯按 `user_type=='individual'` 卡,会把
staff/遗留非 individual 行新 404 掉（行为回归）。结果:本地 Power 用户拿到完整
计费面板,纯本地用户名用户干净 404,云端零回归。**旧的
`_require_cloud()`（`is_cloud_mode` 门禁）已删除** —— 那会把 JWT 安全 regime
和"是否 Power 账号"两件事混为一谈。旧笔记里"加写操作前需明确绑定该边界"的
待办至此落实:写操作现在都过 `is_power_account`。

## 2026-07-05 — recharge routes (Phase 4, module E)

`POST /recharge` (RechargeRequest{amount>0, currency=USD} → hosted Stripe checkout, reuses
`_validate_checkout_url` MITM guard on the returned URL). success_url/cancel_url are
**deliberately NOT accepted** from the client — an unvalidated redirect target into a payment
session is attack surface with no current use; NetMind's default result page is used and we poll
by-session (matches the client docstring). `GET /recharge/{session_id}` (by-session poll):
session_id is allowlisted `^cs_[A-Za-z0-9_]+$` BEFORE splicing into the outbound path (blocks
`..`/`?`/`#` smuggling). Error mapping: auth→401, Forbidden→403, NotFound→404, business→400,
upstream→502. amount≤0 rejected by Pydantic (422) before any upstream call. Client shape in
[[netmind_billing_client]].



# billing.py — NetMind 计费/订阅代理路由（`/api/billing`）

## Phase 3 新增（2026-07-02）

`POST /subscribe` `/cancel` `/reactivate`——共用 `_write_action` harness（cloud
门禁 + 本地身份 + netmind token + 统一错误映射）。错误三分：`BillingBusinessError`
→**400**（透传 user-safe message，如"Already subscribed"）、`BillingAuthError`
→401、`BillingUpstreamError`→502。subscribe 返 Stripe checkout_url，前端引导支付
后轮询 `/me`。

**审查加固**：① `_validate_checkout_url` —— subscribe 返回的 checkout_url 必须
https + host 属 `*.stripe.com`，否则 502（防被 MITM/被黑的上游喂 openExternal 恶意
URL，安全 HIGH）；② `/plans` `/subscription` 读路由**也** catch
`BillingBusinessError`→502（共享 `_request` 对任何非鉴权 4xx 抛它，读路由不 catch
会 500——Phase 3 引入的回归，已修）；③ `action: Literal[...]` 而非 str。

## 为什么存在

D-1 决策：计费 API 走**后端代理**（避 CORS、统一持凭证、未来 key 落库），不让
前端直连 NetMind。本路由把前端持有的 NetMind `loginToken` 转发到 NetMind 计费
API，只加 HTTP 信封、cloud 门禁、错误映射。委托给 [[netmind_billing_client]]。
注册在 [[main]]，prefix `/api/billing`。

## 上下游

- 上游：前端 `api.getPlans()` / `api.getSubscription()`（[[api]]，经
  `X-Netmind-Token` 头带 loginToken）；[[NetmindAccountPanel]] 消费。
- 下游：[[netmind_billing_client]] → NetMind 计费域。

## 踩过的坑 / 设计决策

- **cloud 门禁用规范判定器** `utils.deployment_mode.is_cloud_mode()`（认
  `NARRANEXUS_DEPLOYMENT_MODE`），**不是** `providers.py::_is_cloud()`（只看
  sqlite、不认 env）——否则本地 cloud-smoke 打不开 gate、语义也不规范。
- **身份分层**：`/subscription` 先 `resolve_current_user_id`（本地身份门禁，挡
  未登录）再取 NetMind token。但本地 user_id 目前只做"是否登录"的存在性门禁，
  **不与 NetMind 账户做绑定校验**——授权边界委托给 NetMind（token 谁的就是谁的
  数据）。Phase 1 只读自己数据可接受；**加写操作（subscribe/cancel）前需在
  Phase 2/3 明确绑定或显式记录该边界**（安全审查 #3）。
- **错误映射**：`BillingAuthError`→401（前端据此重新走 NetMind 登录）、
  `BillingUpstreamError`→502（不把上游不可用伪装成用户凭证错）。客户端响应永远是
  固定信封/固定 detail，不泄漏上游 body/栈/token。
- **`/api/billing` 在 [[auth]] 的 `QUOTA_BYPASS_PREFIXES`**：超额用户正是最需要看
  "升级 Pro" 面板的人，不能被 402 挡在门外（安全审查 M-1）。
- **前端 401 特判**：billing 的 401（NetMind token 失效）**不得**触发全局
  `narranexus:auth-expired` 登出——见 [[api]] 的 `isBillingEndpoint` 跳过逻辑。

## Phase 2（2026-07-02）— GET /fee-info

代理 `/v1/finance/user-fee-info`（余额/eligibility，模块 B）。同 _write 之外的读
模式：cloud 门禁 + 本地身份 + netmind token；BillingAuthError→401、
(BillingUpstreamError|BillingBusinessError)→502。

## G1 增强（2026-07-03）— GET /records

代理 `/v1/finance/records`（消费+充值流水，模块 B）。可选 `direction=expense|income`。
解包 `{data, has_next}`。NetMind 上此接口后，模块 B 从「只有混算余额」升级到「有真实流水」。
