---
code_file: backend/routes/billing.py
last_verified: 2026-07-30
stub: false
---

## 2026-07-30 — 付款后跳回站内：`_return_urls`（取代下方 2026-07-05 的判断）

线上 P0：用户在 agent.narra.nexus 付完款，Stripe 把他们丢到 NetMind 自己的
结果页（一个陌生域名）。根因不是"跳转坏了"，而是**跳转目标从来不属于我们**
—— Checkout Session 由 NetMind 创建，`success_url`/`cancel_url` 存在那个
session 上，我们唯一的杠杆就是调用时把这两个字段递上去。

`_return_urls(flow)` 生成这对 URL，落点 `/app/settings?tab=account&status=…&flow=…`
（`subscription` / `topup`）。三条判断值得记：

- **origin 只来自部署配置 `settings.public_base_url`，绝不来自请求头**。
  支付 session 的跳转目标不能被调用方影响；而部署值本身已经可信，所以这里
  **不需要**再叠一层 allowlist —— 那只是仪式，不是第二道防线。
- **只接受 https + 公网 host，其余一律降级为不传**。判据全部来自实测，不是猜的
  —— 猜错的代价是用户**根本付不了款**，装饰性的跳转永远不值这个（铁律 #16）：
  - 非法 URL → 上游 500 `Failed to create Stripe checkout session`。
  - **loopback / 私网 host → 上游边缘 HTML 403，任何 scheme 都拦**
    （`https://localhost` 与 `http://192.168.x.x` 一样）。而我们的 client 把 403
    映射成 `BillingAuthError`、路由再报 **401「NetMind token 无效或已过期」** ——
    发出去不但毁掉付款，还会把锅甩给用户的登录状态。所以加了
    `is_obviously_non_public_host` 这道筛（[[url_safety]]，同步、不做 DNS：付款
    路由不该因为一次 DNS 抖动改变行为）。
  - **注意「上游拒绝 http」是错的**：`http://example.com` 实测 200。坚持 https
    的真实理由只有一条 —— Stripe **live 模式**对纯 http 回跳 URL 的行为我们没验，
    而两边代价不对称（拒 http 只是让 http 自托管者少一个跳转；发了被 Stripe 拒
    就是付款直接坏掉）。
  - 推论：**`bash run.sh` 的本地用户同样拿不到回跳，但那是上游拦的，不是我们的
    规则误伤** —— 即使我们发 `http://localhost:5173`，那一整个建 session 的调用
    也会被 403 掉。
- origin 取自 `netloc` 但**剥掉 userinfo**（`rpartition('@')[2]`）：userinfo 必须走
  —— `user:pass@` 的 base URL 会把基础认证凭据泄露给 NetMind 并存进 Stripe session；
  而 netloc 是唯一能保住 **IPv6 方括号**的形式（`.hostname` 会剥掉方括号，再拼回端口
  就得到 `https://2001:db8::1:8443` 这种畸形 URL，发上去照样毁付款）。
- **解析和校验必须在同一个 try 里**，任何畸形配置一律降级返回 `{}`，绝不 500 ——
  未捕获就是两个付款端点齐齐 500，正是这个函数存在的目的所要避免的结果。会抛
  ValueError 的有两处，都不明显：
  - `urlparse` **自身**在两类 netloc 上抛：NFKC 归一化会改变字符串的（**全角冒号
    `：`** —— 这个值要手敲进 EC2 的 .env，中文输入法下是最可能的手抖）、以及 IPv6
    方括号不配对。
  - `parsed.port` 是个会解析的 property，`:99999` / `:abc` 抛，而 `.scheme` /
    `.hostname` 都不抛 —— 所以前面几道筛全都正常通过，异常落在最后一步。
    写成 `_ = parsed.port` 而不是裸表达式语句：ruff 的 `select = ["E","F"]` 不含
    B018，裸语句在后来者眼里像死代码，会被顺手删掉。
- **处置与 [[url_artifact]] 的 `_origin_tuple` 同款** —— 它早就为这同一个 setting
  guard 了 `.port`（`except ValueError: return None`）。所以这不是首次发现，而是
  仓库里已有的先例；它自己的 `urlparse` 调用同样裸着，属既有问题，已记 todo。
- **解析通过≠可用**：ASCII 空格、零宽空格能过 urlparse 和 host 筛，然后作为畸形 URL
  抵达 Stripe 换回一个 500 —— 同一族的粘贴/输入法问题，只是失败点在上游。所以拿
  origin 之前还加了一道 `netloc.isascii()` + 无空白字符。用国际化域名的部署需要配
  punycode（`xn--…`，是 ASCII）。

以上三条演进都由 PR #211 的两轮评审抓出，起因是自审时那版「hostname + port」重建。
- 于是**没配 `PUBLIC_BASE_URL` 的部署行为与修复前逐字节一致**（自托管、以及
  桌面端——它的前端 origin 是 `tauri://localhost`，Stripe 根本跳不回去）。
  云端把 `PUBLIC_BASE_URL` 设上是这个修复真正生效的开关，见 `.env.cloud.example`。

`_write_action` 因此多了 `extra` 形参：只有 subscribe 需要这对 URL，
cancel/reactivate 不开 Stripe checkout，把参数留在调用点而不是塞进 harness
里，是让这条约束不会随手被破坏的原因（有测试钉住）。

**下方 2026-07-05 条目中"success_url/cancel_url **deliberately NOT accepted**"
的判断已被本次取代**：当时拒绝的是**从客户端 body 透传**未校验的跳转目标，
那个判断至今有效且仍然成立 —— 现在的 URL 由后端自己算，从不接受客户端输入。

### 上游行为（2026-07-30 dev 实测，两条链路都已验证）

- **recharge**：合法 URL → 200 建新 session；非法 URL → 500
  `Failed to create Stripe checkout session`。字段确实转交给 Stripe。
- **subscribe**：本地 cloud 模式 + `PUBLIC_BASE_URL=https://agent.narra.nexus`
  真实走完一次 test-mode 付款，浏览器**落回 agent.narra.nexus**，账号 free →
  ACTIVE Pro。上游默认落地页是别的域名，所以这只可能来自我们传的 `success_url`。
- **pending session 幂等窗口**：探测早期误判为长期问题。实测是 11:19–11:22 三次
  不同 body 返回同一 session（那三次都没碰 Stripe），11:47 再调就建了带新 URL 的
  session —— 窗口 3–25 分钟之间。**存量 pending 用户不会长期卡在旧落地页**；
  只有几分钟内的连续重试会看到参数改动不生效。
- 上游**不校验 URL 格式**，只当 str 收下 —— 合法性必须我们这侧兜完。

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
