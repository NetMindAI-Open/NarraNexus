---
code_dir: src/xyz_agent_context/message_bus/
last_verified: 2026-04-10
stub: false
---

# message_bus/ — Agent 间通信基础设施

## 目录角色

`message_bus/` 提供 Agent 之间异步通信的完整基础设施。它与 `channel/`（IM 渠道，面向人机交互）的区别在于：MessageBus 专为 Agent 与 Agent 之间的消息传递设计，是系统内部的"内网通讯"，不暴露给外部用户。

核心架构是：`MessageBusService` 抽象接口 + `LocalMessageBus` SQLite 实现 + `MessageBusTrigger` 事件驱动轮询引擎。`module/message_bus_module/` 里的 `MessageBusModule` 是暴露给 Agent LLM 的 MCP 工具层（不在这个目录里）。

## 关键文件索引

| 文件 | 职责 |
|------|------|
| `message_bus_service.py` | 抽象接口定义：发消息、管频道、发现 Agent、投递追踪 |
| `schemas.py` | 四个数据模型：BusMessage、BusChannel、BusChannelMember、BusAgentInfo |
| `local_bus.py` | SQLite/MySQL 后端实现，cursor-based 投递模型 |
| `cloud_bus.py` | 云端实现占位（全部 NotImplementedError） |
| `message_bus_trigger.py` | 后台轮询引擎：检测待投递消息并触发 AgentRuntime 处理 |

## 和外部目录的协作

**被谁启动**：`message_bus_trigger.py` 作为独立进程运行（`uv run python -m xyz_agent_context.message_bus.message_bus_trigger`）；`services/message_bus_poller.py` 提供轻量的手动触发函数。

**调用谁**：`MessageBusTrigger` 调用 `AgentRuntime.run()` 把待处理消息投递给目标 Agent；投递成功后调用 `LocalMessageBus.ack_processed()` 推进游标；失败时调用 `record_failure()` 记录。

**message_bus_module/ 的关系**：`module/message_bus_module/` 里的 MCP 工具（`send_message`、`get_unread` 等）调用 `LocalMessageBus` 的具体方法。MessageBusTrigger 和 MessageBusModule 是对同一个 bus 实例的不同访问入口——前者是"系统驱动 Agent 处理消息"，后者是"Agent 主动使用消息能力"。

## 消息路由机制

`MessageBusTrigger._should_process_message()` 实现了 mention 过滤规则：
- 私信（direct 频道）：总是处理
- 群组频道：channel 创建者（owner）总是被激活；其他成员只在被 `@agent_id` 或 `@everyone` 点名时才处理
- 任何人不处理自己发的消息（`msg.from_agent == agent_id` 跳过）

这个设计决策是为了防止"Agent A 发消息触发 Agent B → Agent B 回复触发 Agent A → 无限循环"。群聊里的非 owner 成员不会因为无关消息被激活，大幅减少无效的 AgentRuntime 调用。
