---
code_file: src/xyz_agent_context/agent_runtime/agent_runtime.py
last_verified: 2026-04-28
stub: false
---

## 2026-04-28 change — trace injection + LoggingService removed (M4 / T15)

`run()` now opens an `ExitStack` around its body and binds two
contextvar scopes via `xyz_agent_context.utils.logging.bind_event`:

1. Outer scope (entire run): `run_id` (fresh `run_<uuid8>`),
   `agent_id`, `user_id`, and the optional `trigger_id` from
   `trigger_extra_data`. Every log line emitted by Steps 0-5 carries
   these.
2. Inner scope (after Step 0 yields): `event_id = ctx.event.id`,
   stacked on top of the outer scope. Lines from Step 1 onward also
   carry this.

This is the mechanism behind the operator's "grep one event_id, get
the whole turn" workflow. See `_setup.py` for the format string that
prints `{extra[run_id]}` and `{extra[event_id]}` on every line.

The injected `LoggingService` argument and the `_logging_service`
field are gone. They previously called `setup()` per `run()` to add
a per-agent file sink; that design leaked file descriptors on EC2 (a
multiprocessing.SimpleQueue per `enqueue=True` sink, leaking 2-3 fd
when cleanup didn't run, saturating the jobs container at 1021/1024
fd in 3 days). File logging now lives at the process level inside
`setup_logging()`, called once at startup. The background hook task
no longer needs to drive an `async_cleanup()` finally — cost context
clearing is the only thing that survived in that block.

Constructor signature shrank: `AgentRuntime(database_client=...,
response_processor=..., hook_manager=..., use_async_db=...)`. Any
caller still passing `logging_service=` will get TypeError; this is
intentional (no back-compat per ironclad rule #2).

## 2026-04-20 change — LLM resolver error path (Bug 2 + Bug 18)

- Catches the new base class `LLMResolverError` (covers both
  `LLMConfigNotConfigured` and `SystemDefaultUnavailable`) instead of
  only `LLMConfigNotConfigured`. Yields a structured `ErrorMessage`
  with `error_type=<subclass name>` so trigger-layer consumers can
  pick per-type UX (see `agent_runtime/run_collector.py`).
- Before the early `return`, best-effort persists the error as
  `event.final_output = f"[ERROR:{type(e).__name__}] {e}"` via
  `event_service.update_event_in_db`. Without this the Event row
  created by Step 0 sat with `final_output=NULL` forever (Bug 18 —
  failed turn invisible to audits / events-table-based UI).
- The user's original input stays preserved in `events.env_context.input`
  (Step 0 already wrote it); this patch only closes the missing
  `final_output` gap. Writing a full failed-turn record into
  `chat_module` instance memory is intentionally deferred until Bug 8
  (failed-turn filtering on retrieval) is picked up — landing them
  together avoids polluting chat history with half-failed entries.

# agent_runtime.py — Agent 执行流水线编排器

## 为什么存在

一次 agent turn（用户发消息 → agent 回复）涉及 10+ 个子步骤、4 种服务、多层持久化，还要处理 LLM 配置加载、取消信号、cost tracking 等横切关注点。把这些逻辑塞进一个函数会是屎山。`AgentRuntime` 是一个纯粹的 Orchestrator——它不包含任何业务逻辑，只负责按序调用各 step 函数，传递 `RunContext`，并 yield 进度消息给 WebSocket。

## 上下游关系

上游：`backend/routes/` 中的 WebSocket 端点实例化 `AgentRuntime` 并调用 `run()`，同时可以通过 `CancellationToken` 发送停止信号。各种 trigger（`bus_trigger`、`job_trigger`）也直接实例化 `AgentRuntime.run()`。

下游：所有步骤函数（`step_0_initialize` 到 `step_5_execute_hooks`），以及 `EventService`、`NarrativeService`、`SessionService`、`HookManager`、`ResponseProcessor` 等服务。File logging 不再属于 AgentRuntime 的职责——由 `utils.logging.setup_logging()` 在进程启动时一次性配置。

依赖注入：`ResponseProcessor`、`HookManager` 通过构造函数注入，方便测试时替换。数据库客户端通过 `db_factory.get_db_client()` 懒加载单例，不在 `cleanup()` 中关闭（共享单例，不能在局部关闭）。

## 设计决策

**`user_id` 被替换为 agent owner**：`run()` 入口立即把 `user_id` 覆盖为 `agents.created_by`。原始 `user_id` 代表触发者（可能是 Matrix 消息发送者、job target 等），但 narrative/context 要基于 owner 的工作空间来查找，否则不同 trigger 来源会落到不同的 narrative 空间里。这个替换是静默的，只在 log 里可见。

**Steps 5-6 推到后台**：用户的 WebSocket 连接在 Step 4 完成后就可以关闭（final_output 已经 yield 出去了）。Steps 5-6（hook 执行、callback 触发）是后处理，用 `asyncio.create_task()` 推到后台运行，不阻塞响应。`asyncio.create_task` 自动复制当前 contextvars，所以后台任务里 `[BG]` 日志行依然带着原 turn 的 `run_id` / `event_id`，可被同一个 grep 拉出来。后台 task 不再需要驱动日志 sink cleanup（M4/T15）。

**每次 run() 重新初始化服务**：`EventService`、`NarrativeService`、`SessionService` 等在每次 `run()` 里重新创建，不复用跨 turn 的状态。这避免了状态泄漏，代价是每次都有轻量的初始化开销。

## Gotcha / 边界情况

- `_execute_callback_instance()` 是递归调用——它在后台创建新的 `AgentRuntime.run()`。如果 callback chain 很深或有循环依赖，可能导致无限递归。目前没有 depth limit 保护。
- `cleanup()` 不关闭数据库连接（特意设计），注释有说明。如果在测试里手动调用 `cleanup()` 后再查数据库，连接仍然存在（来自 db_factory 单例）。
- `bind_event(event_id=...)` 只在 `ctx.event is not None` 时进入（Step 0 在异常路径下可能不创建 event），所以早期失败的 turn 日志只带 `run_id`，没有 `event_id`——这是有意的，避免对未持久化的 event 做无意义的引用。
- Cost tracking 的 `set_cost_context` 是 task 级别的 ContextVar，`clear_cost_context` 在后台任务的 `finally` 里执行，确保不泄漏到其他任务。

## 新人易踩的坑

- `run()` 是 async generator，必须用 `async for msg in runtime.run(...)` 消费，不能用 `await`。WebSocket handler 必须 iterate 完整个 generator，否则后台 Steps 5-6 的 `asyncio.create_task` 不会被调度（generator 还没运行到那一行）。
- `forced_narrative_id` 参数用于 Job trigger，跳过 Narrative 选择直接用指定 Narrative。如果传了不存在的 ID，会 fallback 到正常选择流程，这是有意的降级。
