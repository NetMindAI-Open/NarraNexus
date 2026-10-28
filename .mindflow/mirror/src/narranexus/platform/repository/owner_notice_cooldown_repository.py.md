---
code_file: src/narranexus/platform/repository/owner_notice_cooldown_repository.py
last_verified: 2026-09-09
stub: false
---

# owner_notice_cooldown_repository.py — owner 系统通知的持久化冷却窗

## 为什么存在

`owner_notice_cooldowns` 表（2026-09-09 新增）的唯一读写方。owner 收件箱的
SYSTEM_NOTICE 写入方（目前是 [[message_bus_trigger]] 的永久失败通知与「没回复」通知）
需要回答「这件事上次告诉 owner 是什么时候」。此前这个答案是 trigger 进程里的一个
dict，三个后果都在上游 issue NetMindAI-Open/NarraNexus#106 那类交接失联里付过账：

- **重启即忘**：每次部署后的第一轮 poll 会把窗口内的通知再写一遍；
- **多容器各算各的**：两个 trigger 进程各写一条；
- **按类别塌缩**：key 是 `agent:category`，channel A 上一次永久失败会把 channel B 上
  一次无关失败的通知按掉整整 30 分钟——正是「交接静默失联」的形状。

## 设计决策

- **窗口长度不在这里**。表只记 `last_notified_at`；多长算冷却是写入方的常量
  （`FAILURE_NOTIFY_COOLDOWN_SECONDS`），`is_cooling(..., window_seconds)` 由调用方传入。
  这样不同通知面可以共用一张表而各自定窗。
- **键是 (agent_id, target, category)**。`target` 对 bus 来说是 channel_id；命名成
  target 而非 channel_id 是留给别的 owner 通知面（例如后台 LLM 告警可用 source 名）。
- **只用 `get_one / update / insert`**，没有手写 SQL，两方言天然一致；仍配了
  `tests/repository/test_owner_notice_cooldown_repository_mysql.py` 钉 DATETIME(6)
  经 `coerce_utc` 的往返与复合主键。
- `arm(at=...)` 的 `at` 参数只为测试植入已过期的窗口；生产调用方不传。
- 读失败由调用方决定方向：trigger 选择 **fail-open（照样通知）**——重复一条比永远
  漏一条便宜。

## 上下游

写：`MessageBusTrigger._notify_owner`（成功写入收件箱之后才 `arm`，与原先「先写成功再
武装」的纪律一致）。读：同一函数入口处 `is_cooling`。

## Retention

`cleanup_older_than_days(days)`（2026-09-09，review I1）：按 `last_notified_at` 删过期窗口，一条 raw DELETE（MySQL 孪生已钉）；调用方给的天数必须远大于最长窗口，否则会把活窗口扫掉。
