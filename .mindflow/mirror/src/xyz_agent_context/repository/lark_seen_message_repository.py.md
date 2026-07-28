---
code_file: src/xyz_agent_context/repository/lark_seen_message_repository.py
last_verified: 2026-04-21
stub: false
---

# lark_seen_message_repository.py — 持久化 Lark event dedup 闸门

## 为什么存在

Lark 的 WebSocket event 投递是 **at-least-once**：客户端 ack 不到位或断连重连，同
一个 `message_id` 会被服务端再次推送。`LarkTrigger` 原来只靠一个 60 秒 TTL 的进程
内 set 去重——进程重启就全忘，超过 60 秒的重投也漏。现场表现就是 an operator 报的"同
一条消息隔了一个小时 agent 又回了一遍"（Bug 27）。

本 repo 提供一个最小闸门：**atomic "是否已见过 message_id"**，落 DB 所以跨进程
持久；内存层留给 trigger 做 hot cache（hit 就不到这里来）。

## 上下游关系

**被谁用**：`module/lark_module/lark_trigger.py` 的
`LarkTrigger._should_process_event` —— 事件进队列前的最后一道闸门。trigger 启动
时也会调 `cleanup_older_than_days` 一次做表内存量管理。

**调用谁**：`AsyncDatabaseClient`（`utils/db/db_factory.get_db_client`），表名
`lark_seen_messages`（见 `utils/db/schema_registry.py`）。

## 设计决策

**不继承 `BaseRepository`**：entity 极简（只有 id + timestamp），需要的两个
操作都是非标准形式（atomic-insert-or-fail、TTL-based bulk delete）。套 CRUD
框架反而变复杂。

**`mark_seen` 的语义是 "try to claim the id"**：First call 插入成功→返回 True，
告诉 caller "go process"；任何后续 call 会撞 UNIQUE → 返回 False，告诉 caller
"already seen, drop"。这让调用方不用先 SELECT 再 INSERT，从根上避免并发 worker
之间的竞态——DB 的 UNIQUE 约束是唯一真源。

**错误文本匹配 SQLite + MySQL**：通过 `"UNIQUE constraint failed"` /
`"Duplicate entry"` / `"1062"` 同时识别两个驱动的 integrity error。更严谨的做法
是 import 两个 driver 的异常类，但会把 driver 细节拖进 repo 层。

**Fail-open on unexpected errors** (2026-04-21 修正)：uniqueness 外的 insert 异常
**raise 出去**，由 trigger 层的 `_should_process_event` try/except 兜底走 fail-open
路径（处理该消息 + log 警告）。之前这里 `return False` 当成"已见过"丢弃，看似
保守，实际上在 DB 抖动期间静默丢消息，和 trigger 层注释自述的"silent loss 更糟"
背道而驰。H-3（2026-04-21 audit）：两层策略必须一致 → 现在都是 fail-open。

**保留 7 天**：`DEDUP_RETENTION_DAYS` 常量在 trigger 类上；far longer than any
observed Lark re-delivery window（最长观察到是数小时级）但 schema 简单到 millions
of rows 也几乎不占空间。

## Gotcha / 边界情况

- `cleanup_older_than_days` 用 `_db.execute(..., fetch=False)`——必须传
  `fetch=False` 才会路由到 `execute_write`，否则会走 SELECT 路径返回 `[]`。
  repo 已处理，但若你改其它 DELETE/UPDATE 操作注意同一陷阱。
- `seen_at` 存 ISO-with-space 字符串（sqlite 友好），不是 epoch int。比较时
  靠字典序——ISO-8601 的 `YYYY-MM-DD HH:MM:SS.ffffff` 格式下字典序等价于
  时间序，所以 `WHERE seen_at < :cutoff_str` 是正确的。
- `mark_seen("")` 返回 True（空串 short-circuit）；trigger 层在 `msg_id` 为空
  时自己也会 `return True` 放行。双层都兜底。
