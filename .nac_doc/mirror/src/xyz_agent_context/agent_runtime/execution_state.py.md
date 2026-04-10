---
code_file: src/xyz_agent_context/agent_runtime/execution_state.py
last_verified: 2026-04-10
stub: false
---
# execution_state.py — Agent Loop 执行过程的不可变状态追踪器

## 为什么存在

Step 3.4 的 Agent Loop 是一个流式过程：文本 delta、工具调用、工具输出、思考块、完成标记依次到达。`ResponseProcessor` 处理每条消息时需要知道当前已有多少工具调用（用于给下一个工具调用分配序号），工具输出需要与工具调用按序号对应（用于展示"第 N 个工具执行完了"）。`ExecutionState` 是这个流式过程的累积状态，它的不可变设计（frozen dataclass + 每次更新返回新对象）确保状态变更可追踪，且没有竞态风险。

## 上下游关系

在 `step_3_agent_loop.py` 中创建（`state = ExecutionState()`），传给 `response_processor.process(response, state)`，然后调用 `response_processor.apply_state_update(state, result)` 获取新状态。循环结束后调用 `state.finalize()` 加入最终输出记录，然后从 `state` 中提取 `final_output`、`execution_steps`、token 计数等构建 `PathExecutionResult`。

`ResponseProcessor` 的 `process()` 方法接收 `state` 用于读取 `tool_call_count` 和 `all_steps`，但不直接修改 state——它返回 `ProcessedResponse.state_update` 描述符，由调用方通过 `apply_state_update` 应用。

## 设计决策

**不可变（frozen dataclass + tuple）**：每次"修改"都创建新实例。这让 debug 时可以保留历史快照，同时避免在 async 上下文中的意外共享修改。`all_steps` 用 tuple 而非 list 确保不可变性（append 变成 `old_tuple + (new_step,)`）。

**`tool_output_count` 单独追踪**：工具输出的序号 (`tool_output_count + 1`) 用于在 `all_steps` 里找到对应的工具调用（按顺序匹配第 N 个 tool_call）。不能用 `tool_call_count` 是因为并行工具调用时所有 call 先到达（count 已到最终值），第一个 output 才到，序号对不上。

**`accumulate_usage` 而非 `set_usage`**：token usage 来自 `response.done` 事件，可能多次到达（多轮 agentic 循环）。累加而非覆盖确保总 token 数正确，`total_cost_usd` 同理。

## Gotcha / 边界情况

- `finalize()` 只有在 `final_output` 非空时才添加最终步骤记录。如果 agent 没有输出文本（只有工具调用），`finalize()` 返回原始 state，不添加 `agent_final_output` 步骤。
- `get_all_steps_as_list()` 把 tuple 转为 list 返回，方便 JSON 序列化。不要直接操作 `all_steps` tuple，用这个方法。

## 新人易踩的坑

- 工具输出按顺序和工具调用对应的假设在并行工具调用场景是成立的（Claude 并行调用后结果按 call 顺序返回），但如果 SDK 行为改变这个对应关系可能失效。`step_display.py` 里的 `ResponseProcessor._handle_run_item_stream_event` 的 `tool_call_output_item` 处理有注释说明了这个假设。
- `model` 字段每次 `accumulate_usage` 调用都会被最新的覆盖（`model or self.model`），所以最终记录的是最后一次 done 事件的模型名。
