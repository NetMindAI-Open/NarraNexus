---
code_file: src/xyz_agent_context/module/message_bus_module/_message_bus_mcp_tools.py
last_verified: 2026-04-10
stub: false
---

# _message_bus_mcp_tools.py — MessageBus MCP 工具函数集合

## 为什么存在

`MessageBusModule` 通过 MCP 服务器向 LLM 暴露工具，但工具函数的具体实现不应该直接写在 Module 类里（会让 `message_bus_module.py` 变成一个巨型文件，且工具函数需要独立可测试）。`_message_bus_mcp_tools.py` 把所有 MCP 工具的实现提取出来，成为可以独立注册到 MCP 服务器的函数集合。

命名前缀 `_` 表示这是 Module 的私有实现，不被包外直接引用。

## 上下游关系

**被谁用**：`MessageBusModule.get_mcp_config()` 返回的 `MCPServerConfig` 里包含工具列表，MCP 服务器框架（`module_runner.py`）把这些工具函数注册到 MCP 协议上暴露给 LLM。

**调用谁**：每个工具函数接受一个 `port` 参数（MCP 服务器端口）和一个 `get_db_client_fn` 参数（工厂函数，调用时返回 DB 客户端）。工具函数内部用这个工厂函数创建 `LocalMessageBus` 实例，调用 `MessageBusService` 的对应方法。这种依赖注入方式避免了工具函数持有全局 DB 状态。

## 设计决策

工具函数签名遵循系统约定的提取模式（`standalone function taking (port, get_db_client_fn)`）——这是为了避免与 `MessageBusModule` 类的循环导入问题，也让工具函数可以在没有 Module 实例的环境里（比如测试）独立运行。

工具覆盖了 MessageBus 的完整操作面：发消息（`send_message`、`send_to_agent`）、查询（`get_unread`、`get_messages`）、频道管理（`create_channel`、`join_channel`、`leave_channel`）、Agent 发现（`search_agents`、`register_agent`、`get_agent_profile`）。

## Gotcha / 边界情况

工具函数里的错误处理：一般返回 `{"success": True/False, "error": "..." }` 格式，不会向 LLM 抛出 Python 异常。LLM 需要检查返回值里的 `success` 字段来判断操作是否成功。

每个工具调用都会新建 `LocalMessageBus` 实例（通过 `get_db_client_fn()`），而不是复用同一个实例。这不是性能问题——`LocalMessageBus` 的 `__init__` 只接受一个已有的 backend 引用，构造成本极低，且避免了状态共享问题。

## 新人易踩的坑

工具函数名（如 `"send_message"`）就是 LLM 调用时使用的工具名，必须和 MCP 服务器注册时的名称一致。如果修改函数名，需要同时更新 `MessageBusModule.get_mcp_config()` 里注册工具时使用的名称字符串，否则 LLM 调用会报"工具不存在"。
