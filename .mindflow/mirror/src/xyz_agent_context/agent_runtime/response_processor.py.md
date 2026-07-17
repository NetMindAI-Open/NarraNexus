---
code_file: src/xyz_agent_context/agent_runtime/response_processor.py
last_verified: 2026-07-14
stub: false
---

## 2026-07-14 — `response.error` 新增确定性自助类分支（不再被兜底掩盖 / "黑盒" P1）

在 `response.error` 处理里，**auth 判断之后、recoverable 之前**插入一层
`classify_self_serviceable(error_type, error_message)`（来自
`agent_framework/llm_failure`）。命中（context-window / 余额 / 模型 ID）则产出
`ErrorMessage(error_type=config_actionable, severity=fatal,
action_reason=<reason>)` + 由 `self_serviceable_user_message` 组的可操作文案
（引导 + 脱敏 provider 原文，保留 token 数字）。`step_3` 据此 skip 兜底。

这是对 **2026-06-17 那条**的自然演进:那次把 context-length 从 auth 里摘出来
（避免误判成 re-login），当时只能让它"走 recoverable / helper fallback"——但
fallback 正是把失败伪装成正常回复的元凶（本条 P1）。现在给它专属分类 +
正确引导（"换更大上下文的模型"），既不是 auth，也不再被掩盖。顺序:auth 优先
（先判先返回），self-serviceable 次之，最后才是 recoverable 残余桶。

## 2026-06-17 — 收紧 auth 误判:`invalid_request_error` 退出类型集合

`_AUTH_FAILURE_TYPES` 原含 `invalid_request_error`（注释「常包着坏/过期
key」）。但这是 OpenAI 的 **catch-all 客户端错误类型**,同时覆盖坏 key 和
大量非 auth 400(context_length_exceeded / 坏 model id / content-policy)。
codex 把这些原样透传上来,于是每次上下文超长 / 坏 model 的 turn 都被误判成
`auth_expired`(fatal)+ 掐掉 helper fallback。改为:类型集合删掉
`invalid_request_error`;phrase 列表补 `"incorrect api key"`。真正的 auth
仍精确命中——codex 发 `unauthorized`、Claude 发 `invalid_request`(无
`_error`)、OpenAI 坏 key 靠报文 "Incorrect API key provided" 兜住。回归测试:
`test_invalid_request_error_is_not_auth_by_type_alone` +
`test_openai_bad_key_still_classified_by_message`。

## 2026-06-11 — 鉴权失败单独归类为 fatal + auth_expired

`response.error` 分支原来把**所有** API error 一律 `severity="recoverable"`，
本意是别让一次瞬时 rate-limit 把整轮 turn 拆掉。但鉴权失败（codex OAuth
token 过期/refresh 已用过 → `error_type="unauthorized"` + "log out and sign
in again"）是**不可恢复**的——凭证已死，重试或 helper 兜底都没用，而
helper 还会编一个回复把"登录失效"盖住（incident 2026-06-11：每轮静默退化
到 gpt-5，Settings 还显示 "✓ auth ready"）。

新增 `_is_auth_failure(error_type, error_message)`（框架无关，铁律 #9）：先
匹配分类（`unauthorized` / `authentication_error` / 含 "auth"），再兜底匹配
message 片段（"sign in again" / "refresh token" / "401" 等）。命中则发
`ErrorMessage(severity="fatal", error_type=AUTH_EXPIRED_ERROR_TYPE,
error_message=可操作提示)`，提示用户 `codex login` / 换 API key 槽位。
`AUTH_EXPIRED_ERROR_TYPE = "auth_expired"` 是导出常量，step_3 靠它跳过
no_reply fallback（见 step_3_agent_loop 2026-06-11 条目）。瞬时错误仍走
`recoverable`。测试：tests/agent_runtime/test_response_processor_auth_failure.py。

## 2026-05-13 — Phase B：generator 化 + thinking-delta WS 合并

`ResponseProcessor.process(...)` 从 "return 单个 ProcessedResponse" 改成
**generator yield 0..N 个 ProcessedResponse**。同时引入 per-instance
`_ThinkingBatcher`：

- 一个 raw thinking_item 进来：进 batcher，yield 0（仍累积）或 1（触发 chars/time 阈值）
- 一个 raw 非 thinking 事件进来：先 flush thinking 残余（yield 0 或 1 个 THINKING），再
  yield 该事件本身。**两条输出**——保证用户看到 thinking → tool_call 的自然时序
- 100ms / 500 chars / type 切换 / 显式 flush 任一触发都会出 thinking frame
- 一个 turn 一个 ResponseProcessor 实例（per-run），所以 batcher 也是 per-run

新加 `flush_pending(state)` 方法：caller 在 agent_loop 退出后（正常 / 异常 / cancel
都要）显式调一次，把 batcher 里残留的 thinking 吐出去——否则最后一段思考会
silent dropped。`step_3_agent_loop.py` 已经在 try 末尾 + except 块开头都接上了。

数据效果（5000 个 1-char chunk 输入）：emit 出来的 thinking 帧 ≤ 200，content
逐字保留，顺序不变。

**调用方契约变化（重要）**：所有调用 `process()` 的代码必须改成 `for result in
processor.process(...):` 而不是 `result = processor.process(...)`。Stream 结束
后必须 `for result in processor.flush_pending(state):` 收尾。

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
