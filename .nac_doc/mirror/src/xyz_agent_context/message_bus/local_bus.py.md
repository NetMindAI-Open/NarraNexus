---
code_file: src/xyz_agent_context/message_bus/local_bus.py
last_verified: 2026-04-10
stub: false
---

# local_bus.py — MessageBus 的 SQLite/MySQL 实现

## 为什么存在

`MessageBusService` 抽象接口的本地实现。名字叫 "local" 但实际上支持任何 `DatabaseBackend`（SQLiteBackend 和 MySQLBackend 都可以）——"local" 的含义是"非云端 API"，即所有状态存在本地数据库而非远程消息队列服务。

这是生产环境实际运行的实现，`CloudMessageBus` 还只是占位。

## 上下游关系

**被谁实例化**：`message_bus_trigger.py` 里的 `_get_bus()` 工厂函数通过 `LocalMessageBus(backend=db._backend)` 创建实例；`module/message_bus_module/` 的 MCP 工具初始化时也需要一个 `LocalMessageBus` 实例。

**依赖谁**：接受一个 `DatabaseBackend` 实例（不是 `AsyncDatabaseClient`，是更底层的后端接口）；通过 `backend.execute()` 执行原始 SQL；序列化/反序列化 JSON 用标准库 `json`。

## 设计决策

`get_pending_messages()` 的 SQL 逻辑：`created_at > last_processed_at`（或 last_processed_at IS NULL）AND `from_agent != agent_id`（不处理自己发的消息）AND 失败次数 < 3（poison message 过滤）AND 该 Agent 是频道成员（JOIN `bus_channel_members`）。

`send_to_agent()` 的自动创建逻辑：检查两个 Agent 之间是否已有 direct channel（名称用排序后的 agent_id 组合，保证对称唯一），不存在则创建，然后把两个 Agent 都加为成员，最后发消息。这是幂等的——并发发消息可能触发竞态创建两个 channel，但查询逻辑取的是特定名称，后续调用会找到已存在的。

`_generate_id()` 用 `secrets.token_hex(4)` 生成 8 字符的 hex，与系统其他 ID 的生成方式略有不同（其他地方用 `uuid4().hex[:8]`）。功能等价，但格式上 `secrets.token_hex` 是纯十六进制，`uuid4().hex` 也是十六进制——实际是一样的。

## Gotcha / 边界情况

`LocalMessageBus` 接受 `DatabaseBackend` 而不是 `AsyncDatabaseClient`——这个区别很重要。`DatabaseBackend` 是更底层的接口，直接支持 `execute()` 方法。如果你有 `AsyncDatabaseClient` 实例，用 `client._backend` 取底层 backend。

`ack_processed()` 用 UPSERT 逻辑更新 `bus_channel_members.last_processed_at`——如果成员记录不存在（agent 只是消息接收者但不是正式频道成员），这里可能会失败或无效。Agent 必须先通过 `join_channel()` 成为正式成员，`last_processed_at` 游标才能被正常追踪。

## 新人易踩的坑

所有 SQL 里用的是 `%s` 占位符（不是 `?`），这依赖 `DatabaseBackend.execute()` 的参数处理层把 `%s` 自动转成目标数据库的占位符格式。不要改成 `?` 或 f-string 直接拼接。
