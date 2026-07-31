---
code_file: src/xyz_agent_context/agent_runtime/execution_state.py
last_verified: 2026-07-29
stub: false
---
## 2026-07-29 (三次) — record_thinking 增加 monologue 参数

NexusPower 的独白(框架的 assistant text)以 thinking_item 形态展示,此前从不进
`final_output`——于是 `meta_data.reasoning` 持久化、`<my_reasoning>` 回填、helper
fallback 判定在 nexus turn 上全部静默失效。现在 `record_thinking` 的 `monologue`
参数把独白子集追加进 `final_output`(与 claude 驱动 assistant text 走 append_text
同义),CoT 与其他驱动传空串,行为不变。展示流一个字节不动(铁律 #16)。

同时独白段以 `monologue` 字段存进 thinking step dict——events.event_log 因此按
时序携带定位的独白,是 [[history_projection]] 原生回放能重建 assistant 消息的前提
(累计的 final_output 丢失了位置信息,做不到)。

## 2026-07-29 (二次) — 删除 resume_failed / mark_resume_failed(T5)

它们的唯一用途是让 adapter 通知 step_4"这个句柄过期了,删掉那行"。
[[step_4_persist_results]] 的句柄持久化已删除,没有行可删,整条链失去意义。

`cli_session_id` 字段保留:它是从 `ResultMessage.session_id` 读到的观测值,仍进
日志与 `PathExecutionResult`,只是不再有人拿它去查库。

## 2026-07-29 — tool_output 记录配对 id

`record_tool_output` 新增 `tool_call_id` 参数,写进 step。无 id 时存**空字符串
而非伪造**——消费方要能区分"没有 id"和"id 已知",只在必须时才回落到位置配对。

为什么需要:每轮交给 CLI 的 transcript([[transcript]])要从 `events.event_log`
重建 `tool_use` / `tool_result` 配对,而该格式**严格按 id 配对**。而
**并行工具调用**时所有 call 先到达、output 按完成顺序返回,所以"第 N 个 output
对应第 N 个 call"这条假设本身就是错的。错配的 `tool_result` 不是降级,是后续每
一轮 API 400。

顺带修掉一个既有展示 bug:[[response_processor]] 查找工具名也是纯位置配对,
并行调用时前端会显示错的工具名。现在优先按 id、位置降为回落。

# execution_state.py — Agent Loop 执行过程的不可变状态追踪器

## 2026-07-28 — resume_failed 字段 + mark_resume_failed()（resume 化 R3）

新增 `resume_failed: bool = False` 与 `mark_resume_failed()`（不可变模式，
replace 返回新对象）。语义：**置位后粘住**（sticky）——后续任何事件（冷启
动重试的 response.done 等）都不能把它抹回 False。内部信号，不进用户视野；
终点是 PathExecutionResult → step_4 删陈旧句柄。测试：
tests/agent_runtime/test_resume_failed_threading.py。

## 2026-07-27 — streamed_* 兜底 token + finalize() 提升

新增 `streamed_input/output/cache_*` 字段 + `accumulate_streamed_usage()`:承接从
流式 `message_start`/`message_delta` 抠出的每轮 usage(见 [[response_processor]] /
[[output_transfer]]),**与权威的 input/output_tokens 分开存**。`finalize()` 新增
提升逻辑:当权威 input/output 都为 0(CLI 终结 `ResultMessage.usage` 没报——网关
代理的非 Anthropic 模型就是这样)且 streamed 有值时,把 streamed 提升为权威值。
真 Anthropic(DONE 带 usage)时是 no-op,故不会重复计。修免费额度不扣 agent token
的 bug;`finalize()` 的早返回改为「有提升或有 final_output 才 replace」。
## 2026-07-25 — cli_session_id 字段(resume 化 R1)

新增 `cli_session_id: Optional[str] = None` + accumulate_usage 扩参。合并语义
与 num_turns 完全同规:**latest-non-None-wins,绝不累加**——它是单次运行的
标识符不是增量;None 事件不得抹掉已上报的值。None = 框架没报(非 Claude 路径)。

## 2026-07-23 — cache/num_turns 字段 + 全面改用 dataclasses.replace(W1)

新增 `cache_read_tokens`/`cache_creation_tokens`(累加语义,同 input/output)与
`num_turns`(**latest-non-None-wins,绝不累加**——它是框架报的单次运行总数,
不是逐事件增量;None 表示"未上报",与 0 严格区分)。同时把 7 个方法的手工全字段
重建全部改为 `dataclasses.replace(self, ...)`:行为不变,消掉"加一个字段要改
7 处构造"的维护陷阱——本次加字段正是踩着这个陷阱做的最后一次全量手改。

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
