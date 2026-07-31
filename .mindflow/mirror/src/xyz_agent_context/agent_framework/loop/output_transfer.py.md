---
code_file: src/xyz_agent_context/agent_framework/loop/output_transfer.py
last_verified: 2026-07-30
stub: false
---

## 2026-07-30 — tool_call_item 统一构造函数（pending 语义）

三处字面量构造（claude 非流式 new_items / claude 流式 / codex started）
收敛为 `tool_call_item()`。`arguments=None` = 只知道名字（流式参数没到）
→ 标 pending，UI 立刻显示「正在用 X」，随后同 tool_call_id 的完整事件
覆盖。非流式 provider 一次给全、自然只发完整版——这个不对称是 provider
特性，平台不抹平（铁律 #15）。今天三处调用方 arguments 均非 None（codex
`_codex_tool_args` 恒返回 dict），pending 恒 False；构造函数是给 SDK
partial_json 流式路径预留的接缝。

## 2026-07-29 — session_id 降级为纯观测值(T7)

`ResultMessage.session_id` 仍然被抽取、仍然走
response_processor → ExecutionState → PathExecutionResult 这条链,但**已经没有
消费者**:step_4 的句柄持久化删了(T5),表也摘了(T7)。它现在只进日志。

保留而不删的理由:它是"这一轮 CLI 报了什么会话"的唯一观测点,排查 resume 行为时
有用;而且删它要动四层的字段,收益是省一个字符串。注释已改写,免得下一个读者以为
它还被存进库里。

# output_transfer.py — Claude SDK 消息格式转换为统一事件流

## 2026-07-29 — UserMessage 的 TextBlock 不再当作 agent 输出

`_convert_user_to_stream_events` 过去在「没有 ToolResultBlock」时把 UserMessage 的
文本当 text delta 发出去。user 角色承载的是工具结果和 **CLI 自己塞进 transcript 的
东西**，其中最大的一块是 auto-compaction 交接：运行摘要 + CLI session `.jsonl` 的
绝对路径 + "Please continue the conversation from where we left off"。发成 delta 后
被 [[response_processor.py]] 判为 AGENT_RESPONSE、被 [[run_collector.py]] 累进
`events.final_output`，于是 owner 在 Inner Thought 卡片里读到 CLI 的内部记账（还带
一个他打不开的容器路径）当成 agent 的回答。prod 2026-07-29 近 30 天 11 个 agent 命中
60+ 次。

agent 说话只走 StreamEvent / AssistantMessage，所以这条路径上**没有 agent 输出可丢**
——丢掉正是目的。与 [[sdk.py]] 里拦截 `AssistantMessage.error`（避免上游 400 被渲染
成 agent 自己的话）是同一类修复。

## 2026-07-27 — 从流式事件抠 token usage(免费额度记账修复)

`_convert_stream_event_to_stream_event` 现在处理原本被跳过的 `message_start` /
`message_delta`:`message_start.message.usage` 出 **input**(+cache),
`message_delta.usage` 出 **output**,各发一条 `DATA_TYPE_USAGE` 事件。input 只从
message_start 取、output 只从 message_delta 取——LiteLLM 会在 message_delta 里
**冗余回显 input**,这样取法避免重复计。动机:Claude Code CLI 对着**网关代理的
非 Anthropic 模型**时,终结 `ResultMessage.usage` 恒为 0,导致整轮 token 没记、
免费额度不扣。`_convert_result_to_stream_event` 仍照常把 ResultMessage.usage 放进
DONE(真 Anthropic 权威值);两者不会重复计——streamed 值只在 DONE 为 0 时由
[[execution_state]] `finalize()` 兜底提升。下游累加见 [[response_processor]]。

## 2026-07-27 — 事件类型字面量收敛到 loop/events.py 常量

六种事件形状的字符串字面量改为 import `loop/events.py` 的常量
（TYPE_RAW_RESPONSE_EVENT 等），值逐字节不变——纯机械替换，行为零变化。
事件契约自此有唯一事实源，详见 events.py.md。

## 2026-07-25 — session_id 从"只打日志"改为随 data 上抛(resume 化 R1)

W1 留的口子兑现:`data["session_id"] = session_id`(INFO 日志保留),沿 num_turns
的同一链走 response_processor → ExecutionState → PathExecutionResult,由 step_4
落 `agent_cli_sessions`。远程 Executor 路径的事件 dict 经 NDJSON 原样穿透,新键
随 data 自然携带,remote 侧零改动。codex 转换器不产 session_id——该键只在
Claude 路径出现。

## 2026-07-23 — ResultMessage 补提取 num_turns + session_id(W1 token 埋点)

`_convert_result_to_stream_event` 现在把 `ResultMessage.num_turns`(int 才透传)
放进 response.done 的 data,供 response_processor 折算进 ExecutionState——此前
"每次运行 ≈1.8 次调用"只能靠均值相除推算。`session_id` 只打 INFO 日志**不落库**:
它是 `--resume` 可行性实验(E1)的前置证据,列位留待实验结论。cache 两键
(331-334 行)早已提取,本轮零改动,只是下游终于开始消费。
计划:`reference/self_notebook/plans/2026-07-23-token-consumption-optimization.plan.md`。

## 2026-06-17 — reasoning textDelta vs summaryTextDelta 同进 Thinking 面板的安全前提

`item/reasoning/textDelta`(原始 `reasoning_text`)和 `summaryTextDelta`(受控
`summary_text`)都映射成可见的 `thinking_item`。这是**靠不变式安全,不是靠运
气**:codex 只有在 `show_raw_agent_reasoning` 打开时才流原始 `textDelta`,而我们
**从不设它**(config 只写 `model_reasoning_summary="detailed"`)→ OpenAI 受控
CoT 模型只会出 `summaryTextDelta`,原始思维链不外泄。`textDelta` 只有对**原生
暴露 reasoning 的 provider**(DeepSeek-R1 等)才有内容,那种情况下显示就是预期
UX。**若哪天开了 `show_raw_agent_reasoning`,必须回来改这个分支**,否则会把
OpenAI 原始 CoT 泄给用户。仅加注释,无行为变更(PR #25 评审 M3)。

## 2026-06-11 — codex `error` 通知字段不可信，逐层判型再 `.get`

`_codex_official_to_openai_agents` 的 `_METHOD_ERROR` 分支原来写
`info = err_obj.get("codex_error_info") or {}` 后直接 `info.get("type")`。
`or {}` 只挡 None/falsy —— 但真实 codex `error` 通知里
`codex_error_info` 会是**非空字符串**（如 `"stream_error"`），于是
`info` 是 str，`info.get("type")` 抛 `AttributeError: 'str' object has
no attribute 'get'`。

后果是一条**完整因果链**：该异常从 `CodexSDKv2.agent_loop` 的
`async for` 循环里冒出 → `[AGENT-LOOP-FATAL]` → Step 3 落到
`[FALLBACK] mode=no_reply` → helper LLM 接管出可见回复。表现为用户侧
**每轮 codex 都退化成 helper**，且 **Thinking 面板恒空**（reasoning
还没翻就崩了，`reasoning_chars=0`）。更糟的是它**盖住了 codex 真实
的报错内容**——崩在翻译层，codex 到底报什么错根本没进日志。

修复：抽出 `_codex_error_fields(err_obj)` helper，被 `_METHOD_ERROR` 和
`turn/completed`（status=failed）两个分支共用。`payload.error` 可能是
dict（TurnError）也可能是裸 str（传输层失败）；`codex_error_info` 可能
是 dict 也可能是 str。每一层都先 `isinstance` 判型。**关键**：当
`codex_error_info` 是字符串（如 `"unauthorized"`、`"stream_error"`）时，
**把它原样作为 `error_type` 透出**，而不是丢成泛泛的 `"error"`——下游
`response_processor` 靠这个分类识别鉴权失败并提示重新登录（见该文件
2026-06-11 条目）。回归测试见 test_output_transfer_codex_official.py 的
`test_top_level_error_*` / `test_turn_completed_failed_unauthorized_*`。

> 后续验证发现 codex 每轮发 error 的**真实根因**就是
> `codex_error_info: "unauthorized"`（OAuth refresh token 已用过）——
> 即 codex 登录失效。止血后该真实 message 才得以浮现。

**同日补充——丢弃 `will_retry=True` 的瞬时重试错误**：codex 重连掉线
的流时会连发 `error` 通知，payload 带 `will_retry: True`、message 形如
`"Reconnecting... 2/5"`。这是 codex 自己的内部重试，不是最终结果，原来
被当成 `response.error` 一条条推到前端，刷出一堆假错误气泡。现在
`_METHOD_ERROR` 分支开头先看 `payload.will_retry`，为真则 drop（DEBUG
日志）。最终结果仍由非重试的 `error` 或 `turn/completed(status=failed)`
透出。测试：`test_transient_retrying_error_is_dropped` /
`test_non_retrying_error_still_surfaces`。

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

被 `adapters/claude/sdk.py` 的 `agent_loop` 方法调用：每收到一条 Claude SDK 消息，就调用 `output_transfer(message, transfer_type="claude_agent_sdk", streaming=True)` 获取一个事件列表，然后 yield 到外部。

下游是 `response_processor.py`，它解析这些事件字典并转换为 schema 中定义的 `AgentTextDelta`、`AgentThinking`、`AgentToolCall`、`ProgressMessage` 等类型化对象。

这个文件完全无状态，是纯函数集合，没有任何数据库或配置依赖。

## 2026-05-31 — Codex CLI 事件必须对齐内部事件格式

Codex `codex exec --json` 的原生事件不是直接喂给
`ResponseProcessor` 的 wire format；这里必须转成项目内部已经消费的
shape。文本要输出 `raw_response_event.data.type="response.text.delta"`，
而不是 OpenAI 原生的 `response.output_text.delta`，否则
`ResponseProcessor` 不会 append text，Step 3 会落到 `no_reply`。同理：
reasoning 要转成 `run_item_stream_event` 的 `thinking_item`，`turn.completed`
要转成 `raw_response_event/response.done`，error 要填 `error_message` /
`error_type` 字段。

## 设计决策

**一条消息可能产生多个事件**：`AssistantMessage` 中可能有多个 `ToolUseBlock`（并行工具调用），`UserMessage` 中可能有多个 `ToolResultBlock`。因此返回类型是 `List[Dict]`，而不是单个 dict。`adapters/claude/sdk.py` 中对结果 `for event in events: yield event`。

**AssistantMessage 中的 TextBlock 和 ThinkingBlock 被跳过**：启用 `include_partial_messages=True` 时，文本和思考内容会先通过 `StreamEvent` 逐 token 流式到达，再以完整内容通过 `AssistantMessage` 到达。为避免重复，`_convert_assistant_to_stream_events` 只处理 `ToolUseBlock`，跳过文本和思考块。

**AssistantMessage.error 字段的错误路径**：Claude SDK 的 `AssistantMessage` 有 `error` 字段用于表达认证失败、billing 错误、rate limit 等。这个特殊 case 在 `_convert_assistant_to_stream_events` 里优先检查，转为 `response.error` 事件。

## Gotcha / 边界情况

- `ResultMessage.usage` 在 Claude SDK 中是 `dict[str, Any] | None`，不是对象。代码里用 `isinstance(raw_usage, dict)` 判断，而不是用 `getattr`，这是有意识的处理。但如果 SDK 更新返回对象形式，这里会静默地取不到数据。
- `include_partial_messages=True` 会导致 partial `AssistantMessage` 也携带 `ToolUseBlock`，造成同一个 `tool_call_id` 出现多次。去重逻辑在 `adapters/claude/sdk.py` 里的 `seen_tool_call_ids` set 处理，不在这个文件里。

## 新人易踩的坑

- 扩展支持新 SDK（如 Vertex AI）时，需要在 `output_transfer()` 的 `if transfer_type ==` 分支里添加新的转换函数，并确保输出的事件格式与现有 `raw_response_event`/`run_item_stream_event` 格式一致，这样 `response_processor.py` 无需修改。
- `_empty_delta()` 是哨兵值，表示"没有内容但不是错误"，`response_processor.py` 里会过滤掉空 delta（`if not delta: return ProcessedResponse(..., message=None)`）。不要误以为空 delta 是 bug。
