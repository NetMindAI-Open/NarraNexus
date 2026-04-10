---
code_file: src/xyz_agent_context/agent_runtime/response_processor.py
last_verified: 2026-04-10
stub: false
---
# response_processor.py — Agent Loop 原始事件 → 类型化消息的转换器

## 为什么存在

`ClaudeAgentSDK.agent_loop()` 产生的事件字典格式是系统内部约定的中间格式（由 `output_transfer.py` 生成），不直接是前端期望的 WebSocket 消息格式。这个文件把原始事件解析为类型化的 schema 对象（`AgentTextDelta`、`AgentThinking`、`ProgressMessage` 等），同时计算出对 `ExecutionState` 的更新操作，让 `step_3_agent_loop.py` 的逻辑简洁干净（只需调用 `process` + `apply_state_update` + `yield`）。

## 上下游关系

被 `step_3_agent_loop.py` 在 Agent Loop 中循环调用：每收到一个 event 字典，调用 `process(response, state)` 获取 `ProcessedResponse`，然后用 `apply_state_update(state, result)` 更新 state，再 yield `result.message`（如果非 None）。

下游消费者：产出的消息对象被 yield 到 WebSocket handler，通过 `step_display.format_tool_call_for_display()` 和 `format_thinking_for_display()` 格式化 ProgressMessage 的展示数据。

`execution_state.py` 是紧密合作的伴随文件——`ProcessedResponse.state_update` 字段存储 state 更新方法名和参数，`apply_state_update` 通过 `getattr(state, method_name)(**args)` 动态调用 `ExecutionState` 的方法。

## 设计决策

**`ProcessedResponse.state_update` 用方法名字符串而非 callable**：这允许序列化（方便调试和测试），也避免了 `ResponseProcessor` 直接 import `ExecutionState` 方法。代价是动态 dispatch（`getattr`）没有静态类型检查。

**工具输出用 `tool_output_count` 匹配对应的工具调用**：在 `_handle_run_item_stream_event` 里，`tool_output_count + 1` 是第几个工具输出，然后遍历 `state.all_steps` 找第 N 个 `tool_call` 步骤，提取工具名用于展示。这个对应关系依赖"工具输出按调用顺序到达"的假设。

**`response.done` 不产生消息**：`response.done` 事件只更新 state 的 token usage，不 yield 任何消息给前端（`message=None`），防止前端显示重复的"完成"指示。

**`response.error` 产生 `ErrorMessage`**：API 认证失败、rate limit、quota 耗尽等错误通过 `AssistantMessage.error` 字段到达，`output_transfer.py` 转为 `response.error` 事件，这里转为 `ErrorMessage` schema 对象 yield 给前端，用户能看到具体错误信息而不是空白回复。

## Gotcha / 边界情况

- 工具调用的步骤序号格式是 `"3.4.{tool_count}"`（字符串），对应前端 ProgressMessage 面板里 Step 3.4.1、3.4.2 等子步骤。工具输出复用同样的步骤序号，前端根据序号更新同一个步骤的状态（running → completed）。
- 非空 delta 过滤：`output_transfer.py` 可能产生空 delta（来自结构性 `StreamEvent`），这里的 `if not delta: return ... message=None` 过滤掉它们，避免前端频繁处理空更新。

## 新人易踩的坑

- `process()` 是无副作用的纯函数，不修改任何状态。需要通过 `apply_state_update()` 才能让 state 变化生效。忘记调用 `apply_state_update` 的话 state 永远是初始状态，工具调用序号会永远是 1。
- `_handle_run_item_stream_event` 里的 `format_tool_call_for_display()` 调用：前端只看到格式化后的展示数据（icon、desc），`tool_name` 原始值也在 `details` 里保留，但 `arguments` 可能因为 `desc_template` 格式化失败而显示为 raw 参数。
