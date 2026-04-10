---
code_file: src/xyz_agent_context/message_bus/message_bus_service.py
last_verified: 2026-04-10
stub: false
---

# message_bus_service.py — MessageBus 统一抽象接口

## 为什么存在

当前实现是 SQLite/MySQL 的本地版本，未来可能迁移到云端消息队列（Redis Pub/Sub、Kafka 等）。`MessageBusService` 抽象类是隔离层，让所有消费方（`MessageBusTrigger`、`MessageBusModule` 的 MCP 工具）面向接口编程，切换实现时不需要修改消费方代码。

这也是系统不强依赖某一个框架原则的具体体现——抽象层允许将来替换底层实现而不破坏上层逻辑。

## 上下游关系

**被继承**：`LocalMessageBus`（SQLite/MySQL 实现）和 `CloudMessageBus`（占位 stub）继承它。

**被消费**：`MessageBusTrigger` 持有一个 `LocalMessageBus` 实例（类型标注是 `LocalMessageBus` 而非 `MessageBusService`，是历史遗留——可以改成 `MessageBusService` 以更严格遵守 LSP）；`module/message_bus_module/_message_bus_mcp_tools.py` 里的 MCP 工具函数接受 `MessageBusService` 参数。

**依赖谁**：`schemas.py` 里的四个数据模型（`BusMessage`、`BusChannel`、`BusChannelMember`、`BusAgentInfo`）。

## 设计决策

投递模型是 **cursor-based**：`BusChannelMember.last_processed_at` 记录每个 Agent 在每个频道里处理到哪条消息，`get_pending_messages()` 返回 `created_at > last_processed_at` 的消息，`ack_processed()` 推进这个时间戳游标。这比"已读/未读"标记更健壮，不需要对每条消息记录处理状态，只需要一个时间戳。

**Poison message 过滤**：连续投递失败 3 次（`failure_count >= 3`）的消息被跳过，防止一条损坏消息阻塞整个队列。失败记录通过 `record_failure()` 累积，`get_pending_messages()` 的实现里需要过滤掉这类消息。

消息有 `mentions: List[str]` 字段，值是 agent_id 列表或 `["@everyone"]`。`MessageBusTrigger` 用这个字段决定是否激活特定 Agent。

## Gotcha / 边界情况

`send_to_agent()` 是便捷方法，内部会自动创建两个 Agent 之间的私信频道（如果不存在）再发送。`send_message()` 需要提前知道 channel_id，更底层。两者都是合法的发消息方式，但语义不同。

`get_unread()` 和 `get_pending_messages()` 的区别：前者基于"已读游标"（`last_read_at`），后者基于"已处理游标"（`last_processed_at`）。在 MessageBus 里，"读取"（Agent 看到消息）和"处理"（AgentRuntime 处理完成）是两个独立的时间戳，以支持"Agent 看到消息后正在思考"的状态。

## 新人易踩的坑

`MessageBusService` 是纯 ABC，不含任何实现。直接实例化会报错。所有使用时应该实例化 `LocalMessageBus(backend=...)` 或通过 `_get_bus()` 工厂函数获取。
