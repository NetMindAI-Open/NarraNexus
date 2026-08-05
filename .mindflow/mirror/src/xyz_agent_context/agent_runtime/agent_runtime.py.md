---
code_file: src/xyz_agent_context/agent_runtime/agent_runtime.py
last_verified: 2026-08-05
stub: false
---

## 2026-07-30 — 打断连续性:先持久化、再让位(interrupt continuity)

两处配套改动。(1) Step-3 消费不再「取消即 break」——那正是被打断 turn 从历史里消失
的原因:驱动响应取消后会自终止并吐出收尾(合成配对/turn_done/PathExecutionResult),
break 恰好把这段尾流扔掉。改为 `_stream_step3_with_interrupt_drain`:未取消时把
「下一条消息」与 `await_cancelled()` 竞速(取消可能落在无界 await 期间——不竞速就是
挂死洞,同时刻只允许一个 anext task 在飞),取消后有界排空(INTERRUPT_DRAIN_BUDGET_S,
超时 aclose 放弃,Stop 永远能完成)。(2) `raise_if_cancelled` 从 Step 4 之前移到
4.6 之后:被打断 turn 照常走 step_4(event_log)+hook_persist_turn(聊天行),带
`execution_result.interrupted=True`;尾流没到就按 silent 先例伪造最小结果保住
user 行。Step 5/6 后台钩子仍被跳过,BackgroundRun 的 CANCELLED 终态路径不变。

## 2026-07-15 — MCP 管道改名 `mcp_urls`/`mcp_server_urls` → `mcp_servers`

值类型从 url 字符串升级为 spec 对象 `{"url": str, "headers": {str:str}?}`，
支撑用户 MCP 自定义请求头（Authorization 等）贯穿全链路。本文件仅机械跟随
改名/类型，职责不变。

## 2026-07-02 — `silent: bool = False` kwarg on `run()`

`run()` now accepts `silent: bool = False`. When True, steps 0-2.5 run
normally (event created, narrative selected, modules loaded, instances
synced) but step_3 (agent LLM invocation) is skipped; a minimal
`PathExecutionResult(final_output="", ctx_data=<from ctx>)` is
fabricated so step_4 / hook_persist_turn / step_5 read a consistent
result. This is the memory-only path used by IM triggers (Matrix /
Lark / Slack, via [[channel_trigger_base]]) for group non-@ messages
and reconnect burst backfill: chat_history writes, observation
extraction, entity_description updates all still fire; the agent just
doesn't reply.

Per-message batch metadata (event_id / timestamp / sender_id / content
/ attachments) travels in `trigger_extra_data["batch_messages"]` and
is picked up by ChatModule's silent-batch write path (see
[[chat_module.py]]). The default `silent=False` keeps owner-facing
runs byte-identical — no regression on the WS / A2A / job paths. See
`tests/agent_runtime/test_silent_mode.py` for the seam locks.

## 2026-05-20 — Step 4.6: synchronous turn persistence before background

`run()` now awaits `hook_manager.hook_persist_turn(ctx.module_list,
build_after_execution_params(ctx))` AFTER Step 4 (`step_4_persist_results`) and
BEFORE `asyncio.create_task(_run_hooks_background())`. Why: Steps 5–6 run in a
background task that can lag 3–19s; the conversation row written there (ChatModule)
was raced by fast user replies → the next turn read history missing the exchange
("short-reply amnesia"). The sync phase makes that write durable in-request. Placed
AFTER Step 4 specifically so the P3 narrative-routing rebind (4.0, see
[[step_4_persist_results.py]]) has already repointed the chat instance — the message
lands in the thread it now belongs to. Param-building is shared via
`build_after_execution_params` (see [[step_5_execute_hooks.py]] / [[hook_manager.py]]
/ [[base.py]]).

## 2026-05-31 — owner LLM config includes Codex runtime config

`run()` now calls `get_agent_owner_runtime_llm_configs()` instead of the
older three-config resolver. The resulting bundle includes `CodexConfig`,
which is written to the current task's ContextVar via `set_user_config`
before Step 3 resolves `user_slots.agent_framework`. Without this,
`codex_cli` dispatch could pick `CodexSDK` but still run with an empty
Codex model/provider/auth config.

## 2026-05-19 — LLMResolverError downgraded from logger.exception to logger.warning

The `except LLMResolverError` branch previously did `logger.exception`,
which emits ERROR + full traceback on every occurrence. For a single
user with an exhausted free-tier quota this fires once per scheduled
job — 1458 lines in 14h on EC2 jobs container 2026-05-19, drowning out
real errors. The exception class itself carries an actionable user
message ("Either turn off 'Use free quota' in Settings ..."), and per
铁律 #15 the platform must not auto-switch providers — so the traceback
adds zero diagnostic value. Now we log a single WARNING line with
type + message and continue surfacing the structured `ErrorMessage`
unchanged.

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

## 2026-07-07 — Step-5 后台 hooks 注入 owner 的 Helper LLM

`_run_hooks_background`（`asyncio.create_task` 脱离任务）此前不继承 run() 设的 per-turn
helper 配置，其 Step-5 LLM hooks（社交实体摘要、memory extraction）一路回退到平台
`settings.openai_api_key`（2026-07 事故）。现在协程开头 `inject_owner_helper_credentials(_agent_id, db)`：
解析失败（配额/无 provider）→ 告警 + 跳过（绝不落平台 key）；hooks 内凭据类异常
（`is_credential_error`）→ `alert_background_llm_failure`，不再仅记日志。

## 2026-07-07 (PR#68 review) — post_turn_hooks 告警补上 owner

`_run_hooks_background` 此前丢弃 `inject_owner_helper_credentials` 的返回值、两处告警写死 `owner_user_id=None`,导致 Step-5 hooks(entity/memory)路径的凭据失败只落审计、进不了 owner inbox(正是事故里静默退化那条路)。现在捕获 owner 并传给告警;ProviderResolverError 分支额外查 agents 表补 owner。通用 except 保持 continue(而非 narrative updater 的 skip)——已加注释说明:Step-5 非 LLM hooks + Step-6 callback 仍需执行,需要凭据的 LLM hook 在内层 except fail-fast 并告警。

## 2026-07-07 (bug#3) — set_user_config 传 cli_helper

run() 起始的 `set_user_config(owner_configs...)` 增传 `owner_configs.cli_helper`，订阅 helper 才能 在本次 turn 生效。

## 2026-08-05 — [turn-timing]：每 turn 一条三段计时线

8/1 活动测得「单条回复 1-7 分钟」但说不出慢在哪段（Base recvrdLPavdQgU）。
run() 现在打三个 monotonic 桩,在 Step 4.6 之后落一条 grep 稳定的日志：
`[turn-timing] agent= event= source= setup_s=(Step0-2.5) loop_s=(Step3)
persist_s=(Step4+4.6) total_s= interrupted=`。Steps 5-6 本来就在后台,不计入。

**核查结论（本批不改行为的依据）**：三个 surface 都没有「事后钩子阻塞送达」
——chat 的回复在 Step 3 流式已到用户;bus DM 的送达是 loop 内工具调用;
team 房的 post 确实等 run() 返回,但中间只有 DB/文件写（narrative 的 LLM
更新是 create_task）。是否值得进一步压 persist 段,由这条计时线的真实数据
决定——测量先行。

### 2026-08-05 R2（review 修正）：total 覆盖 run() 全程 + pre_s 段

R1 的 total_s 从 Step 0 才起算,漏掉 run() 前段（懒加载 DB client、agent
查询、service 构造）——连接池排队恰好发生在那里,却被排除在测量外（review
指出,chat 路径无外层计时可反推）。修正：起算点提前到 run() 入口,新增
`pre_s=` 字段（入口→Step 0）。格式提取为模块级纯函数 `_turn_timing_line`
并有格式测试钉住（tests/agent_runtime/test_turn_timing_line.py）——纯观测
代码的 format 坏掉时会在持久化后、后台 hook 前炸主链路,所以必须可测。
同函数两个 time 别名合并为一个 `_time`。
