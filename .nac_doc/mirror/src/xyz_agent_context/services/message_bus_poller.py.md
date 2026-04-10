---
code_file: src/xyz_agent_context/services/message_bus_poller.py
last_verified: 2026-04-10
stub: false
---

# message_bus_poller.py — MessageBus 轻量轮询辅助函数

## 为什么存在

这个文件是在 `MessageBusTrigger`（`message_bus/message_bus_trigger.py`）正式落地前的过渡期产物——它提供一个简单的 `poll_message_bus()` 函数，可以在 `ModulePoller` 的轮询循环里被调用，或在集成测试里手动调用，而不需要启动完整的 `MessageBusTrigger` 进程。

现在 `MessageBusTrigger` 已经实现，这个文件的主要价值变成了提供一个比 `MessageBusTrigger` 更简单的接口用于测试和一次性手动操作。

## 上下游关系

**被谁用**：主要用于测试场景（`scripts/test_mcp_direct.py` 等）；`ModulePoller` 的扩展版本可以把这个函数集成到同一轮询循环里。当前生产路径使用的是独立的 `MessageBusTrigger` 进程，不经过这个文件。

**依赖谁**：只依赖 `message_bus.MessageBusService` 抽象接口，不绑定具体实现（`LocalMessageBus` 或将来的 `CloudMessageBus`）。

## 设计决策

`poll_message_bus()` 是模块级别的纯函数，而不是类——因为它的逻辑足够简单：遍历 agent_ids、取 pending 消息、log + ack。没有状态需要维护，不需要封装成类。

"处理"的逻辑目前只有 log + ack，实际的 AgentRuntime 回调触发留给调用者自己决定——这是刻意的，让这个函数可以在不启动 AgentRuntime 的场景下独立运行（比如只想清理积压消息）。

每条消息独立 try/except，失败时调用 `bus.record_failure()` 记录，不中断其他消息的处理。这和 `MessageBusTrigger` 的策略一致。

## Gotcha / 边界情况

这个函数的 `agent_ids` 参数需要调用方自己提供——它不知道系统里有哪些 Agent。在生产环境里，`MessageBusTrigger` 通过查 `bus_channel_members` 表动态发现所有有消息的 Agent，而这个函数做不到这一点。

`ack_processed()` 使用的是消息的 `created_at` 时间戳作为游标——如果数据库里时间戳字段是字符串类型（SQLite 场景），需要确保 `str(msg.created_at)` 和 `ack_processed` 里的时间比较逻辑一致。

## 新人易踩的坑

这个文件不是 `MessageBusTrigger` 的替代品，它们是不同层次的工具：`message_bus_poller.py` 是低级辅助函数，`MessageBusTrigger` 是完整的事件驱动轮询服务（有 mention 过滤、rate limiting、adaptive polling、AgentRuntime 集成）。生产中只应该运行 `MessageBusTrigger`。
