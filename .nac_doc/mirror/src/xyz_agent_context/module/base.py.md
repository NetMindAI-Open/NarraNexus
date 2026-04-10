---
code_file: src/xyz_agent_context/module/base.py
last_verified: 2026-04-10
---

# base.py — Module 基类契约

## 为什么存在

`XYZBaseModule` 是整个模块系统的唯一公共契约。它把"一个 Module 必须能做什么"从"某个具体 Module 怎么做"中分离出来。`AgentRuntime` 和 `HookManager` 只依赖这个抽象，从不直接引用具体模块类。

## 上下游关系

- **被谁用**：`ModuleLoader`（`_module_impl/loader.py`）通过 `MODULE_MAP` 按名实例化子类；`HookManager` 循环调用 `hook_data_gathering` / `hook_after_event_execution`；`ModuleRunner` 调用 `create_mcp_server()` 部署 MCP 进程
- **依赖谁**：`DatabaseClient`（`utils/`）同步 wrapper；`AsyncDatabaseClient` 通过 `utils/db_factory.get_db_client()` 懒加载（MCP 进程专用）；`schema/` 中的 `ModuleConfig`、`MCPServerConfig`、`ContextData`、`HookAfterExecutionParams`

## 设计决策

**MCP 数据库连接用类变量隔离**：MCP 服务器作为独立进程/线程运行，不能共享主进程连接池。`_mcp_db_client` 是类变量，每个具体子类在自己的运行环境里各持一个连接，第一次调用 `get_mcp_db_client()` 时懒创建。被否决的方案是让 MCP 工具通过内部 HTTP API 向主进程取数据——会引入额外网络跳转且 MCP 工具的低延迟要求不允许。

**`hook_data_gathering` 和 `hook_after_event_execution` 均有默认空实现**：大多数模块只需要实现其中一个。强制所有子类都实现两个 hook 会造成不必要的样板，且某些仅提供 MCP 工具的模块（如 `BasicInfoModule`）根本不需要 hook。

**`get_instructions()` 用 `ContextData` 做动态格式化**：指令字符串里可以有 `{awareness}`、`{jobs_information}` 等占位符，在 `get_instructions()` 调用时用当前 `ctx_data` 字段填充。子类只需在 `__init__` 里赋值 `self.instructions`。

**`get_mcp_config()` 是抽象方法但允许返回 `None`**：这迫使子类明确表态"我有/没有 MCP 服务器"，而不是漏掉这个决定。没有 MCP 服务器的模块（如 `MemoryModule`）直接 `return None`。

## Gotcha / 边界情况

- **`instance_id` vs `instance_ids`**：前者是"当前这个实例的 ID"，后者是"当前 Narrative 里所有同类实例 ID 列表"（用于 `ChatModule` 加载历史时跨实例查询）。两者都可以为 `None`/空。
- **`create_mcp_server()` 默认返回 `None`**：如果子类没有重写这个方法，`ModuleRunner` 会跳过该模块，静默不报错。

## 新人易踩的坑

- 忘记调用 `super().__init__()` 会导致 `self.agent_id`、`self.db` 等属性 `AttributeError`，错误往往在 hook 执行时才暴露，难以追踪。
- 在 MCP 工具里用 `self.db`（同步 wrapper）而非 `await get_mcp_db_client()` 会遇到事件循环不匹配或跨进程连接共享问题，症状是随机 `RuntimeError` 或连接超时。
- 在 `hook_data_gathering` 里修改了 `ctx_data` 字段后没有 `return ctx_data`，修改会被静默丢弃（特别是并行模式下每个模块拿到的是副本）。
