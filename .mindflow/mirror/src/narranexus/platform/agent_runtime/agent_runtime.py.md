---
code_file: src/narranexus/platform/agent_runtime/agent_runtime.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-04（批 3b）— `run(pipeline_profile=)`、`AgentRuntime(registries=)`

调用方可点名一个注册的 profile（如插件贡献的 `research`），走 `resolve_profile(explicit=)`。构造器可注入私有
`Registries`（测试用；进程注册表在启动后是冻结的，不能往里注册测试策略）。

## 2026-09-04（批 3a）— `run()` 瘦身为「服务装配 + `TurnPipeline.run`」

step_0…step_5 的内联块全部原样搬进 `narranexus.platform.turn.stages`；`run()` 只建 `RunContext`（含 `run_id`）、
`TurnServices`，用 `resolve_profile` 把 fast_mode/silent/TurnProfile 映射成 profile，然后消费流水线的消息流；
Ingress 建出 event 后再绑 trace/cost scope（位置不变）。`_resolve_turn_profile`/`_turn_timing_line`/
`_stream_step3_with_interrupt_drain` 与 `step_*` 名字仍从本模块可取（策略经此调用，测试接缝不变）。

## 2026-08-31 — `run()` 的 cost context 也改成 scope

开头那处 `set_cost_context(agent_id, db_client)` 换成
`_trace_stack.enter_context(cost_context_scope(agent_id, db_client))`，与
Step 0 之后的 `cost_event_scope` 并排。

**理由：`run()` 并不总是最外层。** Step 6 回调经
`_execute_callback_instance` 会驱动**嵌套的 `run()`**，裸 `set` 把父层的
`(agent_id, db)` 永久覆盖掉，父轮回来后再也拿不回自己的。今天两层的
`agent_id` 落回同一个 `self._current_agent_id`、`db_client` 也是同一个单例，
所以看不出任何异常——**第一个**给 `_execute_callback_instance` 传不同
`agent_id` 的调用方出现时，父轮剩余的 post-turn 花销就会记到子 agent 头上，
现象仍然是"账目偏了、不报错"。

这同时补齐了 08-28 留下的不对称：event 那一半当时已经是 scope，
`(agent_id, db)` 这一半还是裸 `set`。两个变量的清理语义详见
[[cost_tracker]] 同日条目（`clear_cost_context` 已收窄为「最外层专用」）。

## 2026-08-28 — 账目也绑 event_id

Step 0 建出 Event 行之后那处 `bind_event` 旁边，多挂一个
`cost_event_scope(ctx.event.id)`，两者同挂在 `_trace_stack` 上，错误路径一起
回退。

理由和日志绑定是同一个：helper LLM 调用（narrative 选择、shutter/decider、
总结、post-turn hooks）在自己的调用点根本拿不到 event_id，此前全部记
`event_id=NULL`，导致单轮 token 只数得到主循环。绑定点必须在 Step 0 之后
——Event 行此时才存在。详见 [[cost_tracker]] 同日条目。

**但两者的守卫条件不同（08-31 修正）**：`bind_event` 保持 `if ctx.event is
not None`——日志继承外层 run_id 正是想要的。`cost_event_scope` 改成**无条件
进入**，没有 Event 行就显式把 ambient 打成 `None`。否则：post-turn 回调触发
的嵌套运行会复制父轮 context，若它自己的 Step 0 没建出 Event 行，它整轮的
helper 花销会记到**父轮的 event_id** 上，父轮卡片的单轮数字被凭空放大——一
个只会表现为"数字偏大"、不会报错的账目错误。

## 2026-08-17 — 去掉 `on_plain_text_delivery`

team 房间的纯文本投递回调没了（见 [[step_3_agent_loop]]），这个参数随之从 `run()` 签名
和 `StepContext` 上删除。没有生产调用方再传它。


## 2026-08-14 — chat fast mode: run() 增 fast_mode 布尔（策略收敛在 runtime）

run() 新增 `fast_mode: bool = False`。trigger 只表达意图（一个布尔），
「fast 意味着哪些 knobs」由模块级纯函数 `_resolve_turn_profile(fast_mode,
turn_profile, working_source)` 统一解析：显式 turn_profile 永远赢（voice
路径零变化，双传时记 debug 日志）；`fast_mode=True` 且无显式 profile 时用
`TurnProfile.fast_for(working_source)`（chat → name="chat_fast"）；缺省
一律 None=今日行为。解析发生在 RunContext 构造前，下游链路零改动。
锁在 tests/agent_runtime/test_resolve_turn_profile.py。

## 2026-08-14 — `[turn-timing]` 保持只进日志

一度加过「同时写 `turn_timing` 表」，已撤回；理由见
`mirror/scripts/diag_collector/latency_report.py.md`。四段的划分与行格式未变。

## 2026-08-14 — Step 5-6 后台任务改走 `spawn`，不再裸 `create_task`

`asyncio.create_task(_run_hooks_background())` 的返回值此前无人持有——教训 #2 的
两半同时中招：循环只持弱引用，逃逸的异常也只在 GC 时冒个 warning。改用
`utils.background_tasks.spawn`，task 名为 `post_turn_hooks:{agent}:{event}`。

职责边界没变：协程内部的 try/except 仍然拥有**领域**失败（凭据告警、owner inbox、
按模块隔离、`clear_cost_context`），`spawn` 只保证任务丢不了、死了有声。

副作用是它顺手给测试开了口子——此前这个 task 没有任何句柄，
`tests/agent_runtime/test_post_turn_hooks_background.py` 靠 `drain()` 才写得出来。

## 2026-08-06 — auto review 收口（PR #247 两轮意见）

review 收口：fast 分支不再整跳 step_1_5，改为 read_history=False 调用（保住轨迹快照与 md 初始化）。

## 2026-08-06 — voice fast mode: 观测（voice-timing + profile 标记）

[turn-timing] 行新增可选尾部 profile=<name>（仅 fast turn 携带；基础形状逐字不变，既有 grep 契约不破——pin 测试同批扩展）。

## 2026-08-06 — voice fast mode: narrative 快路径（BM25 直取）

run() 在 turn_profile.narrative_strategy=="bm25_top1" 时以 step_1_fast_select 替换 step_1 并跳过 step_1_5；普通路径代码原样在 else 分支内，一行未改。

## 2026-08-06 — voice fast mode: TurnProfile 管道（缺省=现状）

run() 新增 turn_profile 参数（None=原行为），透传进 RunContext；fast 分支的编排改动在后续批次。

## 2026-07-30 — 打断连续性:先持久化、再让位(interrupt continuity)

两处配套改动。(1) Step-3 消费不再「取消即 break」——那正是被打断 turn 从历史里消失
的原因:驱动响应取消后会自终止并吐出收尾(合成配对/turn_done/PathExecutionResult),
break 恰好把这段尾流扔掉。改为 `_stream_step3_with_interrupt_drain`:未取消时把
「下一条消息」与 `await_cancelled()` 竞速(取消可能落在无界 await 期间——不竞速就是
挂死洞,同时刻只允许一个 anext task 在飞),取消后有界排空(INTERRUPT_DRAIN_BUDGET_S,
超时 aclose 放弃,Stop 永远能完成)。(2) `raise_if_cancelled` 从 Step 4 之前移到
4.6 之后:被打断 turn 照常走 step_4(event_log)+persist_turn(聊天行),带
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
fabricated so step_4 / persist_turn / step_5 read a consistent
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

`run()` now awaits `hook_manager.persist_turn(ctx.module_list,
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
contextvar scopes via `narranexus.platform.utils.logging.bind_event`:

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
- Cost tracking 的两个 ContextVar 都是 task 级别的。`run()` 用
  `cost_context_scope` / `cost_event_scope` 进出**还原**（挂在 `_trace_stack`
  上，错误路径一起回退），**不是**裸 `set` + `clear`——理由见下方 08-31 条目。
  仍然调用 `clear_cost_context()` 的只有 `_run_hooks_background` 的 `finally`
  （`:1037` / `:1087`）：`spawn` 的 `create_task` 给了它私有的 context 副本，
  在那里清空不会伤到父轮，所以那两处是对的，别跟着改成 scope。

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

## 2026-08-12 — `run()` 透传 `on_plain_text_delivery`

与 `cancellation` 同一条显式参数路径(不塞 `trigger_extra_data` —— 那是个会被序列化的
dict,放 callable 是隐患)。`run_and_collect` 的 `**extra_kwargs` 直通,所以中间没有
任何签名要改。

## 2026-08-21 — live steering 参数穿线(mirror cancellation)

`run()` 新增 `steering`(SteerChannel 活对象),原样进 `RunContext.steering`,再由 step_3 显式传给
`driver.agent_loop(steering=...)`——与 `cancellation` **同一条显式参数路径**,不塞 `trigger_extra_data`
(那是会被序列化并下发给 module 的 dict,放活对象是隐患)。`run_and_collect` 的 `**extra_kwargs` 直通,
中间无签名要改。None = 不可 steer(现状)。

## 2026-09-04 · ingress triggers (batch 3c.3)

`RunContext.registries = self._registries` so the steps' hook calls (step 1 greeting) target the runtime's registries.

## 2026-09-07 — cost scope entered unconditionally again; event binding delegated to the pipeline

The pipeline extraction had moved cost_event_scope inside the yield loop, so every helper-LLM call between Ingress creating the Event and the first yielded message (all of Recall and Compose) — and 100% of a zero-yield turn (silent ingest, callback run) — was booked to the ambient id, i.e. the PARENT event of a nested callback run. Restored the original invariant: cost_event_scope(None) is entered before the pipeline runs (a run with no Event must reset the ambient id, never inherit it; bind_event stays conditional because inheriting run_id in logs is wanted), and TurnServices.bind_event is a callback the pipeline invokes the moment ctx.event exists, entering bind_event + cost_event_scope(event_id) on the same ExitStack so they unwind together on every exit path.

## 2026-09-07 — step bodies and the two turn helpers moved to agent_runtime.steps

The nine step imports kept only so 'ar.step_*' resolved for the turn plugin are gone, together with _turn_timing_line / _stream_step3_with_interrupt_drain / INTERRUPT_DRAIN_BUDGET_S (now public in steps.py). agent_runtime.py imports only RunContext; the stage strategies call the steps through the public surface.

## 2026-09-07（round-2 G2-I3 / A2-10）— the pipeline class comes from the binding, not an import

`from narranexus.platform.turn import TurnPipeline` became
`turn_pipeline_for(self._registries)(self._registries)`. The class moved into `builtin.turn`'s own
package, so the platform must reach it through `bound_entry("turn.pipeline")` — otherwise binding the
slot to `acme.turn` would still leave the builtin implementation imported and live in the process,
and the "replace the whole turn runtime" acceptance scenario would not hold. `resolve_profile` stays
a platform import: choosing the profile is about the turn's facts, not about any one pipeline.
