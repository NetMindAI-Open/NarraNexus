---
code_file: src/narranexus/platform/repository/bus_delivery_receipt_repository.py
last_verified: 2026-09-10
stub: false
---

# bus_delivery_receipt_repository.py — bus 消息在某个收件 agent 处的投递回执

## 为什么存在

`bus_delivery_receipts` 表（2026-09-09 新增）的唯一读写方。bus 的「发送成功」此前只
意味着「行插入了」：收件方 trigger 在这条消息上崩三次、或者跑了一轮什么都没交付，
都只写进**收件方 owner** 的收件箱；发件 agent——刚向自己的用户承诺「开工了」——
什么都不知道（上游 NetMindAI-Open/NarraNexus#106：PM 说 "build is now in progress"，
Web Developer 的 worker 早已在旧模块路径上 import 失败三次）。

## 设计决策

- **一行一个 (message_id, to_agent)，原地更新**——这是回执不是事件日志（重试计数在
  `bus_message_failures`，生命周期在 `service_audit`）。状态机：`accepted` / `held`
  （发送工具的 pre-flight：收件方熔断 PAUSED/COOLING，排队但不会跑）→ `processed`
  （turn 跑了且有人被触达）/ `relayed`（只产出对 owner 的文字，peer 没收到回复）/
  `silent`（跑了谁也没触达）/ `failed`（抛异常，`attempts` 计数）/ `dropped`（到
  poison 阈值，不再重试）。
- **`content_key`** = 收件方那一轮所见正文（按空白规整）的 sha256。只为一个问题存在：
  `prior_outcome(status=…, within_seconds=…)`——「这个收件方是否在窗口内已经在这个 channel
  对**同样内容**到过同一结局（silent / dropped，另一条 message_id）」。**带窗口，绝不永久**
  （review I1/C2）：每日打卡那种重复内容沉默一次不能把链路永远哑掉。这是 [[message_bus_trigger]] 分辨「重发」与「新消息」、
  对同一沉默只唤醒发件方一次的依据（8/31 "This turn ended without delivering a reply"
  乒乓）。
- `reason` 由调用方先 `redact_secrets` 再传入；本层不做脱敏（口径：谁持有原文谁脱敏）。
- 只用 `get_one / get / insert / update`，无手写 SQL；仍有 MySQL 孪生测试钉复合主键
  upsert 与可空列。

## 上下游

写：`_message_bus_mcp_tools._book_receipt`（accepted/held）、
`MessageBusTrigger._stamp_receipts`（其余状态，DM 车道 only）。读：trigger 的
`prior_outcome`。（review M2：曾预留的 `for_sender` / `TERMINAL_FAILURE_STATUSES` 无调用方，按
铁律 #2 删除，连同 `idx_bus_receipt_sender` 索引。）

## 2026-09-10（review r2 I1）— `prior_outcome(exclude_message_ids=…)`

排除项从单个 id 改为集合：调用方传本批全部 message_id（分片按行展开），因为回执是先按整批
写、再问「之前有没有」，只排一条会让多条批次自我抑制。

## Retention

`cleanup_older_than_days(days)`（2026-09-09，review I1）：按 `updated_at` 删旧回执——回执是投递时的事实，读它的窗口关了就是历史，否则表与 `bus_messages` 1:1 永远增长。由 trigger 每日 tick 调（`RECEIPT_RETENTION_DAYS`）。
