---
code_file: src/xyz_agent_context/agent_runtime/_agent_runtime_steps/step_3_execute_path.py
last_verified: 2026-04-10
stub: false
---
# step_3_execute_path.py — 流水线第 3 步：执行路径路由器

## 为什么存在

Step 2 决定了执行路径（`AGENT_LOOP` 或 `DIRECT_TRIGGER`），Step 3 是实际的执行层。但两条路径的实现差异很大（Agent Loop 是流式异步 generator，Direct Trigger 是单次 MCP 调用），如果塞在同一个函数里会很难读。这个文件是轻量路由器：根据 `ctx.execution_type` 分发到 `step_3_agent_loop.py` 或 `step_3_direct_trigger.py`，同时处理 `PathExecutionResult` 与流式事件的分离（前者存到 ctx，后者 yield 给外部）。

## 上下游关系

输入：`ctx.execution_type`（来自 `ctx.load_result.execution_type`）、`ctx.load_result.direct_trigger`（仅 DIRECT_TRIGGER 路径需要）。

输出：`ctx.execution_result`（`PathExecutionResult`，Step 4 用来持久化）。同时 yield 所有流式消息（`AgentTextDelta`、`ProgressMessage` 等）给 `agent_runtime.py` 的 generator，最终到达 WebSocket。

`PathExecutionResult` 是两条路径的统一输出格式，包含 `final_output`、`execution_steps`、token 计数、`ctx_data`（ContextRuntime 的上下文数据，Step 5 的 hook 需要）。

## 设计决策

**`PathExecutionResult` 通过 isinstance 过滤而非专用信号**：`step_3_agent_loop` yield 的消息中，`PathExecutionResult` 是最后一条。这个文件用 `if isinstance(msg, PathExecutionResult): ctx.execution_result = msg` 拦截它，不 yield 给外部（调用方不需要看到这条 result 消息）。其他所有消息正常 yield。这比增加一个专用信号类型更简洁。

**DIRECT_TRIGGER 不 yield 流式消息**：`step_3_direct_trigger` 是 `async def`（不是 async generator），直接 return `PathExecutionResult`。路由器代码里 `ctx.execution_result = await step_3_direct_trigger(...)` 后没有任何 yield，所以 DIRECT_TRIGGER 路径对 WebSocket 来说是静默的（只有后续 Step 4 的 ProgressMessage）。

**`assert ctx.execution_result is not None`**：执行路径结束后如果没有设置 execution_result（逻辑 bug），会以 AssertionError 明确失败，而不是让后续步骤用 None 造成更隐晦的错误。

## Gotcha / 边界情况

- `direct_trigger` 参数的 `params` 字段是 JSON 字符串，需要 `json.loads()` 解析为 dict 后才能传给 `step_3_direct_trigger`。解析失败时 fallback 为空 dict，不报错。

## 新人易踩的坑

- DIRECT_TRIGGER 场景下 `ctx.execution_result.ctx_data` 是 `None`（`step_3_direct_trigger` 不构建 ContextRuntime），Step 5 里读 `execution_result.ctx_data.extra_data` 时要注意 None 检查。
