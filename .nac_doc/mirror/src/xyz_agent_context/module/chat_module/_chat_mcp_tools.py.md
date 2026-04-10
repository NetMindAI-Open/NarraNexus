---
code_file: src/xyz_agent_context/module/chat_module/_chat_mcp_tools.py
last_verified: 2026-04-10
---

# _chat_mcp_tools.py — ChatModule MCP 工具定义

## 为什么存在

从 `chat_module.py` 分离出来（2026-03-06），把 MCP 工具注册逻辑与 Module 的 Hook 生命周期逻辑解耦。`chat_module.py` 专注于记忆管理，这个文件专注于"Agent 如何输出给用户"。

提供两个工具：
- `send_message_to_user_directly`：Agent 向用户说话的**唯一通道**，没有 DB 操作，只返回确认
- `get_chat_history`：直接查询 `instance_json_format_memory_chat` 表返回历史消息

## 上下游关系

- **被谁用**：`ChatModule.create_mcp_server()` 调用 `create_chat_mcp_server(port, ChatModule.get_mcp_db_client)` 创建 FastMCP 实例；`ModuleRunner` 把返回的 mcp 对象部署为服务器
- **依赖谁**：`get_db_client_fn` 注入（`ChatModule.get_mcp_db_client` 类方法）；直接查询表 `instance_json_format_memory_chat`（没有通过 Repository 层，原因见下）

## `agent_id` 如何传入

两个工具都要求 Agent 在调用时传入 `agent_id` 和/或 `user_id`。这是因为 MCP 工具运行在独立进程/线程里，没有"当前 agent 上下文"，必须由 LLM 明确传入。Agent 在系统提示里被告知自己的 `agent_id`（通过 `BasicInfoModule` prompts）。

## 设计决策

**`send_message_to_user_directly` 不写 DB**：工具本身只返回一个成功确认，实际的消息展示依赖于 `AgentRuntime` 监听 `ProgressMessage` 里的工具调用，从 `arguments.content` 里提取内容发给前端 WebSocket。DB 写入在 `ChatModule.hook_after_event_execution` 里完成（提取该工具的调用内容作为 assistant 消息）。

**`get_chat_history` 直接查表而不走 Repository**：历史记录存储在 `instance_json_format_memory_chat` 这个动态命名的表里（表名含模块名后缀），`EventMemoryModule` 的 Repository 层设计没有把这个动态表名暴露为稳定接口。直接查询是权宜之计，表名硬编码是技术债。

**工厂函数模式**：`create_chat_mcp_server(port, get_db_client_fn)` 是工厂函数而非类方法，接受 db client 获取函数作为参数。这是为了在不实例化 `ChatModule` 的情况下创建 MCP 服务器（MCP 进程不持有 Module 实例），同时避免循环引用。

## Gotcha / 边界情况

- **表名硬编码**：`instance_json_format_memory_chat` 是 `EventMemoryModule` 命名约定 `instance_json_format_memory_{module_name}` 的具体化。如果模块名改变，这里也必须同步修改。
- **`check_query` 用 MySQL `information_schema`**：这段表存在性检查代码是 MySQL 专用语法，SQLite 里没有 `information_schema.tables`，会报错。SQLite 环境下 `get_chat_history` 工具会返回错误。

## 新人易踩的坑

- 以为调用 `send_message_to_user_directly` 就完成了响应——工具本身不推送消息，推送是 `AgentRuntime` 在 agent loop 里监听工具调用并转发给前端 WebSocket 完成的。如果前端没收到消息，先检查 WebSocket 连接，而不是检查这个工具。
