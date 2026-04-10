---
code_file: src/xyz_agent_context/module/message_bus_module/message_bus_module.py
last_verified: 2026-04-10
stub: false
---

# message_bus_module.py — MessageBus Module 主体

## 为什么存在

`MessageBusModule` 是 `XYZBaseModule` 的子类，遵循 Module 热插拔协议。它负责两件事：在每次 AgentRuntime 执行前（`hook_data_gathering()`）把 MessageBus 的状态（未读消息、频道列表、已知 Agent）注入上下文；在 MCP 服务器里暴露 MessageBus 操作工具供 LLM 调用。

如果没有这个 Module，Agent 就对 MessageBus 的存在毫无感知——不知道有新消息，也不能主动发消息或管理频道。

## 上下游关系

**被谁加载**：ModuleService 根据 `MODULE_MAP` 在 AgentRuntime 初始化时按需加载；MCP 服务器通过 `module_runner.py` 启动时实例化。

**调用谁**：实例化一个 `LocalMessageBus`（通过 `get_db_client()` 取 backend）；调用 `_message_bus_mcp_tools.py` 里的工具函数暴露 MCP 工具；在 `hook_data_gathering()` 里调用 `bus.get_unread()`、`bus.get_channel_members()` 等取数据。

## 设计决策

Instance 级别是 **Agent-level**（`is_public=True`），即每个 Agent 有一个全局共享的 MessageBusModule 实例，不是每个 Narrative 各自一个。这是因为 MessageBus 是 Agent 级别的通信能力，不需要按 Narrative 隔离。

`hook_data_gathering()` 中注入的消息格式以 `[MessageBus · {from_agent}]` 开头（类似 Matrix 的 `[Matrix · ...]` 前缀），让 continuity.py 的 `_extract_core_content()` 能识别并提取核心内容。如果这个前缀格式改变，需要同步更新 `continuity.py` 的处理逻辑。

在 `WorkingSource.MESSAGE_BUS` 触发路径下，`hook_data_gathering()` 注入的信息会更精简（可能不注入 "已知 Agent" 等非关键列表），以减少 token 消耗——因为此时 LLM 的主要任务是回复特定消息，不需要完整的 bus 状态概览。

## Gotcha / 边界情况

`MESSAGE_BUS_MCP_PORT = 7820` 是该 Module 的 MCP 服务器端口，如果其他 Module 使用了这个端口会发生冲突。新增 Module 时注意检查端口占用。

Module 实例是 Agent-level 的，但 `hook_data_gathering()` 运行时的 `agent_id` 来自 `ctx_data.agent_id`——同一个 Module 实例可能为不同的请求提供服务，不要在实例变量里缓存 agent_id 相关的状态。

## 新人易踩的坑

`MessageBusTrigger`（外部驱动 Agent 处理消息）和 `MessageBusModule.hook_data_gathering()`（Agent 主动查询 bus 状态）是两个独立的机制，可以同时工作。不要误以为开启了 Module 就不需要跑 `MessageBusTrigger`——前者是"Agent 主动感知 bus"，后者是"bus 主动推送消息给 Agent"。
