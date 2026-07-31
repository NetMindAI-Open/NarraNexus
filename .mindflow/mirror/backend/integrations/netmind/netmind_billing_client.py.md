---
code_file: backend/integrations/netmind/netmind_billing_client.py
last_verified: 2026-07-30
stub: false
---

## 2026-07-30 — subscribe/recharge 接受 success_url / cancel_url

两个开 Stripe checkout 的方法各加一对可选跳转参数，调用方（[[billing]] 的
`_return_urls`）从部署配置算出来后传入；**依旧绝不接受客户端输入** —— 下方
2026-07-05 条目反对的是"从 body 透传未校验的跳转目标"，那条判断没有被推翻。

`_redirect_fields()` 只放**调用方真的解析出来的**字段：缺省时是 **omitted 而非
null**。上游两个字段都是 `Optional[str]`、也确实接受显式 null（dev+prod 探测），
但"不带这个 key"才是已经跑了几个月的 body 形状 —— 没配 `PUBLIC_BASE_URL` 的
部署因此逐字节保持修复前的行为。subscribe 无字段时连 body 都不发（`json_body=None`）。

**上游行为实测（2026-07-30，dev）**：

- recharge：合法 URL → 200 建新 session；非法 URL（`"not-a-url"`）→ 500
  `Failed to create Stripe checkout session`。**证明字段真的转交给了 Stripe**，
  同时也说明传垃圾进去会毁掉付款本身，不只是跳转 —— 校验必须在我们这侧做完。
- 上游**不校验 URL 格式**，只当 str 收下，所以别指望它替我们兜底。
- subscribe：**同样已证实转交**（dev 11:47 真实走完一次 test-mode 订阅付款，
  浏览器落回 `agent.narra.nexus`，账号从 free 变 ACTIVE Pro）。NetMind 自己的
  默认落地页是别的域名，所以落回我们的域名只可能来自我们传的 `success_url`。
- **pending session 幂等窗口**（探测中一度以为是长期问题，实测不是）：11:19–11:22
  三次不同 body（空 / 合法 URL / 非法 URL）返回同一个 `cs_test_…`，所以那三次都
  没走到 Stripe；11:47 再调就建了带新 URL 的 session。窗口在 3–25 分钟之间，
  **不是「一直卡着旧 session」**。影响：连续快速重试期间参数改动不生效，仅此而已。

## 2026-07-06 — tighter business-message scrub (pre-push review)

_safe_business_message now also drops any message containing an id/token-shaped
run (8+ chars including a digit), so an opaque session/account id embedded
mid-sentence by a misbehaving upstream can't reach the client/logs. Natural words
(subscription, auto-renew, internationalization) have no digit and are kept.


 
## 2026-07-05 — recharge / by-session (Phase 4, module E)

Added `recharge()` (finance 4.2 HOSTED checkout `/v1/finance/recharge/stripe/checkout`,
returns `checkout_url` — NOT the embedded `client_secret` 4.1 variant, so it fits our
openExternal flow like [[billing]] subscribe) and `recharge_status()`
(`/recharge/by-session/{id}`, status pending/succeeded/failed). Two new errors:
BillingForbiddenError (403 "not your session") + BillingNotFoundError (404), raised ONLY
when a caller opts in via `distinguish_forbidden`/`distinguish_not_found` — recharge_status
opts in so the route can pass 403/404 through instead of collapsing them to 401/400 (every
other endpoint keeps 403→auth). Removed the now-unused `_AUTH_FAIL_STATUSES` constant.



# netmind_billing_client.py — NetMind 计费/订阅 API 代理客户端

## 为什么存在

NarraNexus 云端版要在自己界面里呈现用户的 NetMind 订阅/余额（不跳 NetMind
产品页），且铁律：**不自建计费**——只复用 NetMind 的计费 API。用户的 NetMind
`loginToken`（JWT）由前端持有、每请求经 `X-Netmind-Token` 头带上，后端**转发**
到 NetMind 计费域。这个 client 只包 HTTP 调用 + 错误映射，让 [[billing]] 路由
保持薄。

刻意**镜像** [[netmind_auth_client]]：可注入 `transport`（单测用
`httpx.MockTransport`，零网络）、两值错误契约（`BillingAuthError` → 调用方
401；`BillingUpstreamError` → 502）。

## 上下游

- 上游：[[billing]] 路由（唯一调用方）；base_url 来自 `settings.billing_api_base`。
- 下游：NetMind 计费域（dev `billing.api.protago-dev.com` / prod
  `billing.api.netmind.ai`）。

## 踩过的坑 / 设计决策

- **头名是 `loginToken`（自定义头 + Bearer 前缀），不是 `Authorization`**。
- **鉴权失败状态码：power-subscription 返 401，finance 返 403**——两者都映射为
  `BillingAuthError`（`_AUTH_FAIL_STATUSES`）。
- **绝不 log token**：`headers` 含 token 的 dict 从不进日志；4xx 业务错**只**提取
  `message` 字段（截断 200 字）拼进异常，绝不 dump 整个上游 body（未来 phase 的
  body 可能含 token/支付/PII，且异常 str 会流进服务端日志）——安全审查 M-2。
- **path 永远是调用点硬编码字面量**，无用户可控成分（无 SSRF）。
- Phase 1：`get_plans`（公开）+ `get_subscription`。
- **Phase 3 新增**：`subscribe`（返 checkout_url）/ `cancel`（auto_renew_off）/
  `reactivate`。并加了**第三值错误** `BillingBusinessError`（非鉴权 4xx = 业务
  拒绝，如"已订阅"/"无有效订阅"，caller→400，带 user-safe message）——原本非鉴权
  4xx 一律当 upstream(502) 是错的，业务错该回 400。余额（user-fee-info）、recharge
  仍待后续 phase（B 卡在 403、E 卡在余额回显）。
- **审查加固**：`_safe_business_message` —— 业务错消息即便在 message/detail/error
  白名单键下，也要 scrub 掉 JWT 形状/长无空格 blob（防上游把 token/PII 藏进消息，
  流到客户端/日志，安全 MED）。
- **已知**：dev 上 `user-fee-info` 用 loginToken 返回 403 `Invalid API key`
  （同域 recharge 却正常）——finance 域鉴权异常，待 NetMind 侧确认，属模块 B 门禁。

## Phase 2（2026-07-02）— get_fee_info

`get_fee_info(login_token)` 打 `/v1/finance/user-fee-info`（finance 域，头仍是
`loginToken`）。finance 鉴权失败用 **403**（已在 `_AUTH_FAIL_STATUSES`）→ BillingAuthError。
dev 上此接口一度 403 Invalid API key，现已修复可用；G1 字段缺口（无本周期消耗、
free_credit 混算）仍在，面板降级显示。

## G1 增强（2026-07-03）— get_records

`get_records(login_token, direction?, page_size)` 打 `/v1/finance/records`（finance 域）。
`_request` 加了 `params` 支持 query。NetMind 新上的流水接口，补 G1（消费明细）。
