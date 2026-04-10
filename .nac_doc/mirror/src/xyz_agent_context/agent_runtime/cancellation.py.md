---
code_file: src/xyz_agent_context/agent_runtime/cancellation.py
last_verified: 2026-04-10
stub: false
---
# cancellation.py — 协作式取消令牌

## 为什么存在

用户点击"停止"时，WebSocket handler 需要通知正在运行的 agent loop 停止。直接用 `asyncio.Task.cancel()` 会在任意 await 点抛出 `CancelledError`，难以在中间做清理（如确保 Step 4 仍然持久化已完成的内容）。`CancellationToken` 提供协作式取消：各层在进入昂贵操作之前主动检查 `is_cancelled`，或在自然检查点调用 `raise_if_cancelled()`，确保取消在预期位置发生，而不是随机 await 点。

## 上下游关系

在 `AgentRuntime.run()` 入口创建（如果调用方未传入，则自动创建一个空 no-op token），传入 `RunContext.cancellation` 字段，贯穿所有 step 函数。WebSocket handler 在收到停止信号时调用 `token.cancel()`。

`step_1_select_narrative.py` 有特殊用法：用 `_run_with_cancellation()` 包装 LLM 调用，在取消信号触发时立即 cancel asyncio task，而不是等 LLM 响应完成后才检查。这是为了防止长时间 LLM 调用阻塞取消响应。

`xyz_claude_agent_sdk.py` 在每次收到消息后检查 `cancellation.is_cancelled`，允许在 Claude 流式输出中途中断。

## 设计决策

**`asyncio.Event` 作为底层机制**：相比 `threading.Event`，`asyncio.Event` 是 coroutine-safe 的，`is_set()` 是非阻塞检查，`wait()` 可以用于 `asyncio.wait()` 中的 race 检测（`step_1_select_narrative.py` 里的 `_run_with_cancellation`）。

**`CancelledByUser` 异常而非 `asyncio.CancelledError`**：使用独立异常类型让调用方能精确捕获用户触发的取消，而不会被其他 asyncio 操作的 `CancelledError` 混淆。WebSocket handler 可以区分"用户停止"和"网络断开"等情况。

**幂等的 `cancel()`**：多次调用 `cancel()` 是安全的（`asyncio.Event` 已经 set 后再 set 无效），调用方不需要维护"是否已取消"的状态。

## Gotcha / 边界情况

- `CancellationToken` 基于 `asyncio.Event`，在不同 asyncio event loop 间使用（如用 `asyncio.run()` 创建的新 loop）不安全。实际使用中始终在同一个 FastAPI event loop 里，没有问题。
- `_run_with_cancellation` 的实现里，如果取消信号在 LLM 调用完成的同一毫秒触发，`asyncio.wait` 可能返回两个 done 集合里都有的情况，当前实现优先处理取消（`cancel_waiter in done` 先判断），这是期望行为。

## 新人易踩的坑

- `raise_if_cancelled()` 只有在被调用时才检查，不是自动触发的。如果某个 step 没有调用它，取消信号会被忽略直到下一个检查点。`agent_runtime.py` 中在 Step 2 前、Step 2.5 前、Step 3 消息处理后都有检查点。
- `CancellationToken()` 创建时不需要传 event loop，它内部用 `asyncio.Event()` 延迟绑定到当前 loop。但这意味着在没有 running event loop 的环境（同步测试代码）里创建 token 会 fail。
