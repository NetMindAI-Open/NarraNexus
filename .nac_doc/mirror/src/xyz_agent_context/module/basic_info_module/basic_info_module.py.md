---
code_file: src/xyz_agent_context/module/basic_info_module/basic_info_module.py
last_verified: 2026-04-10
---

# basic_info_module.py — BasicInfoModule 实现

## 为什么存在

BasicInfoModule 是 Agent 了解自身运行环境的最小化通道。它只做一件事：在 `__init__` 里把 `BASIC_INFO_MODULE_INSTRUCTIONS` 赋给 `self.instructions`，让 `get_instructions()` 在每轮对话时把 agent_id、user_id、当前时间等信息注入系统提示。

**没有实现的 hook**：`hook_data_gathering` 和 `hook_after_event_execution` 均使用基类的空默认实现。

**没有 MCP 服务器**：`get_mcp_config()` 返回 `None`，`create_mcp_server()` 返回 `None`。

**MCP 端口**：无。

**Instance 模型**：Agent 级别，capability module。

## 上下游关系

- **被谁用**：`ModuleLoader` 自动加载；`AgentRuntime` 在构建系统提示时调用 `get_instructions(ctx_data)`
- **依赖谁**：`BASIC_INFO_MODULE_INSTRUCTIONS`（`prompts.py`）；无数据库依赖

## 设计决策

**为什么需要一个单独的模块做这件事**：Agent 的 `agent_id`、`user_id`、当前时间这类信息如果硬编码在某个中央 prompt 里，会和 Module 系统的"指令由各模块注入"原则冲突。BasicInfoModule 把这个职责显式化——谁负责告诉 Agent 它是谁，一目了然。

## Gotcha / 边界情况

- `prompts.py` 里的占位符填充依赖 `ContextData` 的字段名精确匹配。如果 `ContextData` 的字段被重命名，`get_instructions()` 的 `.format(**local_ctx_data)` 会抛 `KeyError`。

## 新人易踩的坑

- 这是系统里最简单的 Module，适合作为"新建 Module 的最小参考模板"来理解 Module 的基本结构。唯一不典型的地方是它没有 hook 和 MCP 服务器。
