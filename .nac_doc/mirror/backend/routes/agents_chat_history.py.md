---
code_file: backend/routes/agents_chat_history.py
last_verified: 2026-04-10
stub: false
---

# agents_chat_history.py — 聊天历史与对话记录路由

## 为什么存在

这个文件暴露前端展示对话历史所需的所有读取接口：完整的 Narrative+Event 树（用于调试和归档视图）、简化的时序消息流（用于聊天界面）、单条 event 的 thinking/tool calls 详情（用于懒加载推理过程）。此外还提供清空历史的接口。

## 上下游关系

- **被谁用**：`backend/routes/agents.py` 聚合；前端聊天面板、历史记录页面、调试视图
- **依赖谁**：
  - `InstanceRepository` — 查询 ChatModule 实例
  - `xyz_agent_context.utils.db_factory.get_db_client` — 直接查询 `narratives`、`events`、`instance_narrative_links`、`instance_json_format_memory_chat`、`module_instances`、`agent_messages` 表

## 设计决策

**两套查询路径的双重 fallback**

`get_chat_history` 有两条查询路径。主路径通过 ChatModule 实例的 `instance_narrative_links` 关联找 Narrative，更准确；fallback 路径通过 `narrative_info.actors` 字段过滤，是老版本的查询方式。如果主路径找不到任何 Narrative（比如老数据），自动降级到 fallback。这个设计是为了向后兼容历史数据，因为早期版本没有 instance-narrative 关联表。

**`simple-chat-history` 绕过 Narrative 层**

简化聊天记录接口 (`/simple-chat-history`) 不走 Narrative，直接从 `instance_json_format_memory_chat` 表读取 ChatModule 的 message 数组。这比 Narrative/Event 路径更高效，也更贴近"展示最近 N 条消息"的使用场景。分页用"从最新往旧"的方向切片，而不是传统的 offset/limit，因为聊天界面通常先显示最新消息。

**清空历史的多表级联**

`clear_conversation_history` 不只删 `narratives` 和 `events`，还清理 `instance_json_format_memory_chat`、`agent_messages` 和 sessions 目录下的 markdown 文件。这是因为聊天历史实际上分散在多个存储里，只清一个的话前端会看到数据不一致。

**event log 的 thinking 重组**

Event 的 `event_log` 字段里存的是流式 delta，每个 thinking_delta 是一条独立记录。`get_event_log_detail` 需要把这些 delta 拼接成连贯的 thinking 块，遇到 tool_call 等中断时开启新块。这是懒加载推理详情时在服务端做的重组。

## Gotcha / 边界情况

- **non-chat working_source 的消息过滤**：`simple-chat-history` 对 `working_source != "chat"` 的消息只保留 assistant 角色的消息，过滤掉 user 消息。这是因为 job/matrix 触发的 user 消息是系统生成的触发提示，不应该展示给用户。如果将来有新的 working_source 类型，需要检查这个过滤逻辑。
- **分页方向**：`simple-chat-history` 的 `offset` 参数是"从末尾跳过 N 条"，而不是传统的"从开头跳过 N 条"。`offset=20, limit=20` 取的是倒数 21-40 条，而不是正向的第 21-40 条。
- **timestamp 解析的多格式兼容**：`_parse_timestamp` 需要处理 MySQL datetime（带或不带时区）和 SQLite 文本格式，代码里有一个多格式 fallback 列表。这说明历史数据里存在时间戳格式不一致的情况。

## 新人易踩的坑

删除聊天历史时，不能只删 `narratives` 和 `events` 表。`instance_json_format_memory_chat`（ChatModule 的短期记忆）和 `agent_messages` 表里也有关联数据，不清理会导致下次启动时 Agent 仍然能"记住"被删除的对话。
