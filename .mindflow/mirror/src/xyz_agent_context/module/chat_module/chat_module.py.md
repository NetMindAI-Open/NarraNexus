---
code_file: src/xyz_agent_context/module/chat_module/chat_module.py
last_verified: 2026-04-20
---

# chat_module.py — ChatModule 实现

## 为什么存在

ChatModule 解决两个核心问题：让 Agent 在对话中访问过去的交流历史，以及在对话结束后把这轮对话持久化。它同时定义了"用户可见响应"的提取逻辑——只有通过 `send_message_to_user_directly` 工具发送的内容才算用户可见，Agent 的内部推理过程不记录为 assistant 消息。

**Hook 实现**：同时实现了 `hook_data_gathering`（双轨记忆加载）和 `hook_after_event_execution`（对话持久化）。

**MCP 端口**：7804

**Instance 模型**：Narrative 级别，每个 Narrative 里每个用户有独立的 Chat 实例（`instance_id` 格式：`chat_xxxxxxxx`）。

## 上下游关系

- **被谁用**：`ModuleLoader` 自动加载；`HookManager` 调用两个 hook；`ModuleRunner` 启动 MCP
- **依赖谁**：`EventMemoryModule`（存储后端）；`InstanceRepository`（短期记忆时查找其他 Chat 实例）；`_chat_mcp_tools.py`（MCP 工具实际定义）；`bootstrap/template.BOOTSTRAP_GREETING`（首次对话时注入问候语）

## 设计决策

**双轨记忆的优先级**：EverMemOS 语义记忆（`ctx_data.extra_data["evermemos_memories"]`）优先于 DB 事件记忆。如果 EverMemOS 没有数据（新 Narrative、EverMemOS 不可用），则 fallback 到直接从 `EventMemoryModule` 读取历史。EverMemOS 路径不依赖 `EventMemoryModule`，是更高质量的语义压缩记忆。

**短期记忆移除了时间窗口限制**（2026-02-09 优化）：早期版本限制 30 分钟内的跨 Narrative 消息，但这导致非活跃用户的短期记忆总是空。改为直接取最近 15 条（`SHORT_TERM_MAX_MESSAGES = 15`），不论时间。

**背景任务的 activity record 而非 fake 对话**：当 `working_source != "chat"` 且 Agent 没有调用 `send_message_to_user_directly` 时，不记录一对 user/assistant 消息，而是记录一条 `message_type: "activity"` 的简短描述（如 "Executed a background job"）。防止历史记录被无意义的 "(Agent decided no response needed)" 污染。

**失败轮隔离（Bug 8）**：当 agent loop 抛错时，`_detect_error_in_agent_loop` 从 `params.agent_loop_response` 扫出 `ErrorMessage`（`step_3_agent_loop.py` 在 catch Exception 分支里把 ErrorMessage 既 yield 也 append，保证下游 hook 看得到），`hook_after_event_execution` 只存 user 消息，`meta_data` 里打 `status="failed"` + `error_type=...`，**不写任何 assistant 行**（partial 输出也丢）。下一轮 `hook_data_gathering` + `_load_short_term_memory` 都会过 `_apply_failed_turn_filter`：失败的 user 行被重写成"Previous turn failed... Do NOT retry"的注解（保留原问题文本，方便代词解析），遗留的失败 assistant 行被丢。目的是让 LLM 看到"那轮断了"而不是"那轮我只说了一半还没说完"——后者正是污染下轮 prompt 让 LLM 重复执行上轮查询的根因。

**MCP 工具逻辑抽取到 `_chat_mcp_tools.py`**：2026-03-06 拆分，保持 `chat_module.py` 专注于 Hook 生命周期，MCP 工具注册逻辑独立维护。

## Gotcha / 边界情况

- **Bootstrap greeting 注入**：如果 `ctx_data.bootstrap_active=True` 且是第一轮对话（历史为空），会在写入历史前先插入一条 BOOTSTRAP_GREETING 作为第一条 assistant 消息。这是一次性逻辑，仅发生在 Agent 第一次被激活时。
- **`channel_tag` 的传递**：`hook_after_event_execution` 里从 `ctx_data.extra_data["channel_tag"]` 读取渠道信息（Matrix 房间、发送者等）并写入每条消息的 `meta_data`。如果 `channel_tag` 是 Pydantic 对象（而非 dict），会调用 `.to_dict()` 转换。忘记这个转换会导致 JSON 序列化失败。

## 新人易踩的坑

- 误以为 `instance_id` 就是用户 ID——`chat_xxxxxxxx` 是 Module 实例的 ID，不是用户 ID。一个用户在不同 Narrative 里有不同的 Chat 实例。`get_chat_history` 工具需要的是 `instance_id`，不是 `user_id`。
- 调试时看到 `chat_history` 为空但数据库里有记录——通常是 `instance_id` 不对导致的：ModuleLoader 注入的 `instance_ids` 不包含要查的实例。
