---
code_file: src/xyz_agent_context/agent_framework/output_transfer.py
last_verified: 2026-05-14
stub: false
---
# output_transfer.py — Claude SDK 消息格式转换为统一事件流

## 2026-05-14 — tool_output 必须是干净字符串，不能是 Python repr

`_convert_user_to_stream_events` 处理 `ToolResultBlock` 时，原来用
`str(block.content)` 填 `tool_call_output_item.output`。`block.content`
对于返回 dict 的 MCP 工具（如 `create_artifact`）是一个 content block
**列表**（`[{"type":"text","text":"<JSON>"}]` 或 SDK 的 block 对象），
`str()` 出来是 Python repr —— **不是合法 JSON**。

后果：前端凡是 `JSON.parse(tool_output)` 的消费方（artifact 发现、
quota 错误检测）全部静默失败 —— agent 创建的 artifact 永远不会通过
tool_output 这条路浮现，只能等无关的 reload（切 agent / 收尾刷新）。

修复：新增 `_stringify_tool_result_content()`，把 `block.content`
拍平成真正的文本载荷（str 直接用；list 逐块抽 `text`；SDK block
对象抽 `.text`），保证 `output` 是工具实际返回的字符串。

## 为什么存在

`claude_agent_sdk` 返回的消息类型（`AssistantMessage`、`StreamEvent`、`ResultMessage`、`UserMessage`、`SystemMessage`）是 Anthropic 专有格式。系统的其他部分（`response_processor.py`、前端 WebSocket）期望收到类型化的事件字典（`raw_response_event`、`run_item_stream_event`），与 OpenAI Agents SDK 的流式事件格式对齐。这个文件是格式适配层，把 Claude SDK 的输出标准化为系统内部的事件格式。

## 上下游关系

被 `xyz_claude_agent_sdk.py` 的 `agent_loop` 方法调用：每收到一条 Claude SDK 消息，就调用 `output_transfer(message, transfer_type="claude_agent_sdk", streaming=True)` 获取一个事件列表，然后 yield 到外部。

下游是 `response_processor.py`，它解析这些事件字典并转换为 schema 中定义的 `AgentTextDelta`、`AgentThinking`、`AgentToolCall`、`ProgressMessage` 等类型化对象。

这个文件完全无状态，是纯函数集合，没有任何数据库或配置依赖。

## 设计决策

**一条消息可能产生多个事件**：`AssistantMessage` 中可能有多个 `ToolUseBlock`（并行工具调用），`UserMessage` 中可能有多个 `ToolResultBlock`。因此返回类型是 `List[Dict]`，而不是单个 dict。`xyz_claude_agent_sdk.py` 中对结果 `for event in events: yield event`。

**AssistantMessage 中的 TextBlock 和 ThinkingBlock 被跳过**：启用 `include_partial_messages=True` 时，文本和思考内容会先通过 `StreamEvent` 逐 token 流式到达，再以完整内容通过 `AssistantMessage` 到达。为避免重复，`_convert_assistant_to_stream_events` 只处理 `ToolUseBlock`，跳过文本和思考块。

**AssistantMessage.error 字段的错误路径**：Claude SDK 的 `AssistantMessage` 有 `error` 字段用于表达认证失败、billing 错误、rate limit 等。这个特殊 case 在 `_convert_assistant_to_stream_events` 里优先检查，转为 `response.error` 事件。

## Gotcha / 边界情况

- `ResultMessage.usage` 在 Claude SDK 中是 `dict[str, Any] | None`，不是对象。代码里用 `isinstance(raw_usage, dict)` 判断，而不是用 `getattr`，这是有意识的处理。但如果 SDK 更新返回对象形式，这里会静默地取不到数据。
- `include_partial_messages=True` 会导致 partial `AssistantMessage` 也携带 `ToolUseBlock`，造成同一个 `tool_call_id` 出现多次。去重逻辑在 `xyz_claude_agent_sdk.py` 里的 `seen_tool_call_ids` set 处理，不在这个文件里。

## 新人易踩的坑

- 扩展支持新 SDK（如 Vertex AI）时，需要在 `output_transfer()` 的 `if transfer_type ==` 分支里添加新的转换函数，并确保输出的事件格式与现有 `raw_response_event`/`run_item_stream_event` 格式一致，这样 `response_processor.py` 无需修改。
- `_empty_delta()` 是哨兵值，表示"没有内容但不是错误"，`response_processor.py` 里会过滤掉空 delta（`if not delta: return ProcessedResponse(..., message=None)`）。不要误以为空 delta 是 bug。
