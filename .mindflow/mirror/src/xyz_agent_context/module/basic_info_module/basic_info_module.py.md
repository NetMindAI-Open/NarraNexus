---
code_file: src/xyz_agent_context/module/basic_info_module/basic_info_module.py
last_verified: 2026-07-10
---

## 2026-07-10 — hook 填入真实 LLM 身份（framework + model）

`hook_data_gathering` 新增一段：调 [[agent_model_identity.py]]
`resolve_agent_model_identity(self.agent_id, self.db)`，把
`ctx_data.agent_info_model_type`（framework 展示名，如 "Codex CLI"）+
`ctx_data.model_name`（真实 model，如 "gpt-5"）填上，供 [[prompts.py]] 的
`Your LLM model: **{...}** ({...})` 行渲染。此前这两个值在 [[context_runtime.py]]
被写死成 "Claude Agent SDK / sonnet-4"，导致所有 agent 自称 Claude Sonnet-4
（违反铁律#9）。resolver 绝不抛异常；此处再包一层 try/except，失败也给两字段赋
安全非-None 值，防模板 `.format()` 渲染出 "None"。

## 2026-06-12 — hook_data_gathering resolves identity by human name + real sender

`hook_data_gathering` is now implemented (no longer the base no-op). It fills the
new [[context_schema.py]] identity fields so the canonical identity injection
shows people by name, not the opaque NetMind userSystemCode:
- `creator_id` = `agent.created_by` (kept as opaque scoping key)
- `creator_name` = `UserRepository.get_display_name(agent.created_by)`
- `is_creator` = whether the CURRENT SENDER is the creator. The sender is
  `extra_data['sender_user_id']` when the trigger carries it (chat only), else
  `self.user_id`. **This fixes the old bug** where `is_creator` was derived from
  the runtime `user_id`, which `agent_runtime` always overrides to the owner —
  so a visitor's message was mislabelled as the Creator's.
- `user_role` = "Creator (Boss)" / "User/Customer" from `is_creator`
- `current_speaker_name` = creator_name if is_creator else
  `get_display_name(sender_id)`
On the else/except path names fall back to "Unknown". Consumed by basic_info
[[prompts.py]] (`{creator_name}` / `{is_creator}` / `{current_speaker_name}`;
`{creator_id}` / `{user_id}` were dropped from the human-identity lines).

## 2026-05-20 (Fix #2 P3) — basic_info now hosts the narrative-awareness MCP tools

Previously `get_mcp_config` returned `server_url=""`/`type="None"` (no tools).
Now it advertises an SSE MCP server on `self.port=7808` and `create_mcp_server()`
delegates to [[_basic_info_mcp_tools.py]] (`create_basic_info_mcp_server`),
which registers view_narrative / view_event / switch_narrative /
create_narrative. Port 7808 is registered in [[module_runner.py]]
CORE_MCP_MODULES/CORE_MODULE_PORTS. The tool usage is documented for the agent
in [[prompts.py]] (BASIC_INFO_MODULE_INSTRUCTIONS).
# basic_info_module.py — BasicInfoModule 实现

## 为什么存在

BasicInfoModule 是 Agent 了解自身运行环境的最小化通道。它只做一件事：在 `__init__` 里把 `BASIC_INFO_MODULE_INSTRUCTIONS` 赋给 `self.instructions`，让 `get_instructions()` 在每轮对话时把 agent_id、user_id、当前时间等信息注入系统提示。

**实现的 hook**：`hook_data_gathering` 解析身份的人类名（creator_name / current_speaker_name）并基于真实 sender 计算 is_creator（见上方 2026-06-12 记录）。`hook_after_event_execution` 仍使用基类空默认实现。

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
