---
code_file: src/narranexus/platform/schema/channel_ingress_breaker_schema.py
stub: false
last_verified: 2026-08-27
---

## 2026-08-27 — 新增 `tier_changed_at`

tier 上次变动的时刻，用 guard 的时钟。[[ingress_guard]] 的 `_load` 拿它做
「沉默多久 → 该降几级」的锚点。为什么不是 `last_tripped_at` 或 `updated_at`，
见 [[schema_registry]] 里那一段。

## 2026-08-25（第三轮 review）— 钳制改成 per-column，算术由测试推导

上一条写的「`_MAX_KEY_PART = 128` 让 419/448 由构造保证」**是错的**：
四个分量统一钳到 128 允许 `4*128 + 3 = 515`，超过列宽 448——因为 `channel`
其实只有 VARCHAR(32)。那句「由构造保证」是没做乘法就写下的。

改成 per-column 钳制（128/32/128/128），最坏键长 419。更重要的是
`test_session_key_cannot_overflow_its_column` 现在用
`schema_registry.varchar_width()` **从 DDL 推导**这个上界，而不是在注释里
再复述一遍数字——注释与 schema 漂移正是这次的失败方式。

## 2026-08-25 — 会话键改成四段（含 `agent_id`），并钳制分量长度

**键是 `session_key(agent_id, channel, chat_id, sender_id)`**，不再是三段。
（本页下方 2026-08-24 那条写的三段版本已过时，以本条为准。）

理由：一个 trigger 实例服务全部凭据，同一条房间事件按 agent 扇出，
`_process_message` 每个 agent 各跑一次——与 [[channel_dedup_store.py]] 分区
的理由完全相同，那份 docstring 早写明「a Matrix room event fanned out to
every member agent's client and must each be processed」。漏掉 `agent_id`
会让重复率变成 `1 − 1/N`（N = 房间里我方 agent 数），与内容无关；实测 5 个
agent 的房间里对端发 4 条**各不相同**的消息就跳闸。详见
[[ingress_guard.py]] 的 2026-08-25 条目。

**`_MAX_KEY_PART = 128` 逐分量钳制**：`chat_id` / `sender_id` 来自平台侧、
没有自己的长度契约，所以列宽 448 原本只是一个注释里的算术假设。strict mode
被关掉的 MySQL 部署上，超长键会被静默截断，让两个不同会话撞同一行、互相
覆盖 tier 和 cooldown。钳制让那个算术**由构造保证**而不是靠假设。

# channel_ingress_breaker_schema.py — ingress 熔断器的持久状态模型

## 为什么存在

[[ingress_guard.py]] 的落库那一半。每个会话键一行。

`session_key(channel, chat_id, sender_id)` 是**单一定义**，放在这里而不是
guard 里：内存缓存、DB 行、audit 轨迹三处必须用同一把钥匙指同一个对话，
各拼各的是它们对不上的开始。

## 设计决策

**只有层级变迁落库。** 驱动这些变迁的滑窗计数和内容指纹留在进程内存里——
为 10 分钟就过期的数据每条消息写一行是纯写放大。必须活过重启的是**冷却**：
8/14 那个循环跑了 70+ 小时，期间任何一次重新部署都会把一个已经被隔离 24
小时的对端重新放行，事故就在平台「从没见过这个人」的认知下继续。

**`tier` 是升级记忆，跨发布保留**（同 [[channel_trigger_base.py]] 的
`_breaker_release` 对凭据熔断器的处理）：清完冷却立刻再犯的会话必须落到
schedule 的**下一档**，不是从最短那档重来。

**`suppressed_count` 每次跳闸清零**，所以这个数回答的是「**这一次**隔离
挡下了多少」，而不是一个没有意义的终身累计——它是 owner 通知里的头条数字。

铁律 #14/#15：这张表管的是**入站**准入。它不封顶、不取消、不给运行中的
`agent_loop` 设时限，也不评判 agent 自己的模型或输出，只看进来的流量形状。
