---
code_file: src/xyz_agent_context/agent_runtime/agent_runtime.py
last_verified: 2026-04-10
stub: false
---
# agent_runtime.py — Agent 执行流水线编排器

## 为什么存在

一次 agent turn（用户发消息 → agent 回复）涉及 10+ 个子步骤、4 种服务、多层持久化，还要处理 LLM 配置加载、取消信号、cost tracking 等横切关注点。把这些逻辑塞进一个函数会是屎山。`AgentRuntime` 是一个纯粹的 Orchestrator——它不包含任何业务逻辑，只负责按序调用各 step 函数，传递 `RunContext`，并 yield 进度消息给 WebSocket。

## 上下游关系

上游：`backend/routes/` 中的 WebSocket 端点实例化 `AgentRuntime` 并调用 `run()`，同时可以通过 `CancellationToken` 发送停止信号。各种 trigger（`bus_trigger`、`job_trigger`）也直接实例化 `AgentRuntime.run()`。

下游：所有步骤函数（`step_0_initialize` 到 `step_5_execute_hooks`），以及 `EventService`、`NarrativeService`、`SessionService`、`HookManager`、`LoggingService`、`ResponseProcessor` 等服务。

依赖注入：`LoggingService`、`ResponseProcessor`、`HookManager` 通过构造函数注入，方便测试时替换。数据库客户端通过 `db_factory.get_db_client()` 懒加载单例，不在 `cleanup()` 中关闭（共享单例，不能在局部关闭）。

## 设计决策

**`user_id` 被替换为 agent owner**：`run()` 入口立即把 `user_id` 覆盖为 `agents.created_by`。原始 `user_id` 代表触发者（可能是 Matrix 消息发送者、job target 等），但 narrative/context 要基于 owner 的工作空间来查找，否则不同 trigger 来源会落到不同的 narrative 空间里。这个替换是静默的，只在 log 里可见。

**Steps 5-6 推到后台**：用户的 WebSocket 连接在 Step 4 完成后就可以关闭（final_output 已经 yield 出去了）。Steps 5-6（hook 执行、callback 触发）是后处理，用 `asyncio.create_task()` 推到后台运行，不阻塞响应。后台任务完成后负责调用 `_logging_service.cleanup()`，确保后台日志也写入 agent .log 文件。

**每次 run() 重新初始化服务**：`EventService`、`NarrativeService`、`SessionService` 等在每次 `run()` 里重新创建，不复用跨 turn 的状态。这避免了状态泄漏，代价是每次都有轻量的初始化开销。

## Gotcha / 边界情况

- `_execute_callback_instance()` 是递归调用——它在后台创建新的 `AgentRuntime.run()`。如果 callback chain 很深或有循环依赖，可能导致无限递归。目前没有 depth limit 保护。
- `cleanup()` 不关闭数据库连接（特意设计），注释有说明。如果在测试里手动调用 `cleanup()` 后再查数据库，连接仍然存在（来自 db_factory 单例）。
- Cost tracking 的 `set_cost_context` 是 task 级别的 ContextVar，`clear_cost_context` 在后台任务的 `finally` 里执行，确保不泄漏到其他任务。

## 新人易踩的坑

- `run()` 是 async generator，必须用 `async for msg in runtime.run(...)` 消费，不能用 `await`。WebSocket handler 必须 iterate 完整个 generator，否则后台 Steps 5-6 的 `asyncio.create_task` 不会被调度（generator 还没运行到那一行）。
- `forced_narrative_id` 参数用于 Job trigger，跳过 Narrative 选择直接用指定 Narrative。如果传了不存在的 ID，会 fallback 到正常选择流程，这是有意的降级。
