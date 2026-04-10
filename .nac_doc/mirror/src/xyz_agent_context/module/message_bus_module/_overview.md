---
code_dir: src/xyz_agent_context/module/message_bus_module/
last_verified: 2026-04-10
stub: false
---

# message_bus_module/ — Agent 间通信的 MCP 工具层

## 目录角色

`message_bus_module/` 是 MessageBus 基础设施的 Module 包装——它把 `message_bus/` 的底层能力（发消息、查频道、发现 Agent）封装成 LLM 可以调用的 MCP 工具，并在 `hook_data_gathering()` 里把未读消息和已知频道信息注入 Agent 的执行上下文。

与 `message_bus/` 的关系：`message_bus/` 是基础设施（如何存储和投递消息），`message_bus_module/` 是 Agent 能力层（LLM 如何主动使用消息功能）。`MessageBusTrigger` 是外部触发（收到消息时激活 Agent），`MessageBusModule` 是主动调用（Agent 主动发消息或查询）。

## 关键文件索引

| 文件 | 职责 |
|------|------|
| `message_bus_module.py` | Module 主体：配置、context 注入（hook_data_gathering）、instance 初始化 |
| `_message_bus_mcp_tools.py` | MCP 工具函数集合：send_message、get_unread、search_agents 等 |

## 和外部目录的协作

**被 ModuleService 管理**：通过 `module/__init__.py` 的 `MODULE_MAP` 注册，ModuleService 按需实例化。

**调用 message_bus/**：`_message_bus_mcp_tools.py` 里的每个工具函数都接受一个 `MessageBusService` 实例参数，在运行时注入 `LocalMessageBus`。这保持了工具函数对具体实现的解耦。

**context 注入上限**：`hook_data_gathering()` 注入未读消息（最多 `MAX_UNREAD_IN_CONTEXT=20` 条）、频道列表（最多 20 个）、已知 Agent（最多 50 个）。所有数量都有 cap，防止 MessageBus 数据污染 LLM 上下文。

**WorkingSource 过滤**：当 `working_source == WorkingSource.MESSAGE_BUS` 时（即由 `MessageBusTrigger` 触发的执行），`hook_data_gathering()` 会减少注入的 context 量，避免在消息处理时产生太多噪音。这个过滤逻辑在 `message_bus_module.py` 里实现。
