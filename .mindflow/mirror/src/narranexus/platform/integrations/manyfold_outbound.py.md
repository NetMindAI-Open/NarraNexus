---
code_file: src/narranexus/platform/integrations/manyfold_outbound.py
stub: false
last_verified: 2026-08-10
---

# manyfold_outbound.py — managed-reply 声明 + 平台 channel-send 客户端

## 2026-08-10(review 修)— 迁入 integrations/;env 单一解析;三态结果

1. 从 utils/ 迁到 integrations/(与 feedback_client、free_tier/
   wallet_client 同居):外部平台 wire 契约不是通用工具;
2. 新 `manyfold_runtime_env()` = **身份对(token + runtime_id)的唯一
   解析点**,notify 的 `_webhook_env` 改为委托;`webhook_url` 的必要
   性**属于各 leg**:notify 腿没有它拒绝(空 URL 不可 POST),
   channel-send 腿持显式 URL 时不需要它。共享的是解析,不是三元组
   全有的判定——那个判定曾把逃生口 gate 在它要逃离的东西上;
3. `channel_send` 返回三态 `ChannelSendOutcome`:
   `delivered` / `failed`(403 策略拒绝、5xx、超时——请求可能已达
   平台,**不许**直连兜底:403 兜底=用沙盒凭据绕过目标绑定,超时
   兜底=与平台重试竞态双发)/ `unavailable`(404/405 端点缺失、
   env 不可解析——平台**从未收到**投递请求,直连兜底安全)。

## 为什么存在

托管开关的**单一事实源**。两个消费方共享同一份声明,防止"字段说
managed、出站却直连"的分裂:

1. `backend/routes/manyfold/sync.py` 的渠道 inventory —— 把声明作为
   `config.agent_managed_reply` **显式布尔**下发给平台(平台 mapper
   对缺失键按 managed-ON 处理,#504 起;显式 false 才能把灰度节奏
   留在我方手里);
2. 渠道模块的出站包装(首个:wechat 的 [[wechat_outbound.py]])——
   按同一声明决定"平台代发 vs 直连 provider"。

## 契约(平台 PR #511,已核对 DTO)

`POST <origin>/internal/narranexus-sync/channel-send`,bearer 与 notify
同 token;body **camelCase**:`runtimeId/agentId/provider/roomId/text/
idempotencyKey(+sourceMessageId/attachments[{path}])`。响应
`status ∈ {sent, queued}` 都算成功(queued = 平台已接单并自己重试
provider 腿)。**请求不带收件人、不带凭据**——目标绑定由平台强制
(无入站历史的 room 403)。

## 设计决策

- 声明 = env `NEXUS_MANAGED_REPLY_PROVIDERS`(逗号列表,缺省空 =
  全 false):灰度是配置变更而非代码变更;
- URL 解析:`MANYFOLD_CHANNEL_SEND_URL` 显式覆盖优先——且**无任何
  webhook URL 时也生效**(它存在的理由就是"派生不可能/换 host",
  用 webhook 去 gate 它等于取消它;仅要求身份对);否则从
  `MANYFOLD_SYNC_WEBHOOK_URL` 尾段 `/notify` → `/channel-send` 派生
  (同一平台 controller 的兄弟路由);派生不出 → 拒绝 managed 路由
  而不是瞎猜,调用方回退直连,是安全退化;
- **never-raise,失败一律 False**——与它替代的 `send_text_once` 同一
  错误面,调用点不因路由不同而分裂错误处理;
- `_transport_for_tests` 模块级测试缝,注入 httpx.MockTransport。

## Gotcha

- `managed_channel_send_active` 要求声明 + env **同时**成立:local/
  自托管无 manyfold env,永远直连——部署面不用关声明也安全。
