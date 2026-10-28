---
code_file: src/narranexus/platform/agent_runtime/client.py
stub: false
last_verified: 2026-09-11
---

## 2026-09-11（PR #394 rebase 到 #396 之后）— 自然结束：先 `_finalize_natural_end`，再结算探测

`run_and_collect` / `run_stream` 的自然结束分支现在是 #396 的 `_finalize_natural_end(recorder, STATE_COMPLETED,
STATE_FAILED)`（fatal 落 FAILED + error_message），紧随其后才是本 PR 的 `settle_probe`（有 token 才读结果）——
顺序仍是「events 行终态在前、结算在后」。输出预算耗尽这类 #396 新的真 fatal 以 `succeeded=False` 进
`record_failure`，被 `breaker_exemption` 豁免，持有的探测无结论归还（见 [[circuit_breaker]] 同日条目）。
锁：`test_client_probe_settlement.py::test_an_output_budget_probe_is_released_without_verdict`。
## 2026-09-10（GH #127 / B-05）— natural-end 不再无条件写 STATE_COMPLETED

`run_and_collect`/`run_stream` 里两处 `recorder.finalize(STATE_COMPLETED)`
——生成器**没抛异常**就直接判完成——改调新增的 `_finalize_natural_end()`：
读 `recorder.had_fatal_error`,是就转 `finalize(STATE_FAILED,
error_message=recorder.last_error_message or 兜底文案)`。

根因:一个致命 `ErrorMessage`(死 key、B-03 的截断重试也救不回来)会让
生成器**正常 return**,不是抛异常——异常分支(`except Exception`)一直都
正确写 STATE_FAILED,漏的正是这条"没异常但也没真正成功"的路径。修之前
events 行落地 `state=completed` + `error_message` 为空,Run-observation
侧栏显示一次绿色完成、内容却是空的,ops 只能从
`[AGENT-LOOP-RECOVERABLE]` 之类的应用日志里看出问题(GH #127)。

`had_fatal_error` 早就存在且被 `background_run.py` 的漏斗/熔断信号读过
(见该文件同日条目)——缺的只是"终态持久化也读它"这一步。

测试:`tests/agent_runtime/test_client_recording.py` 的
`test_run_and_collect_finalizes_failed_on_fatal_error_without_exception` +
`test_run_stream_finalizes_failed_on_fatal_error_without_exception`。


## 2026-09-11（PR #394 review 第五轮 M-B）— GeneratorExit 注释改成真实理由

`_spawn_finalize` / `_spawn_settle` 与 `run_stream` 的 GeneratorExit 分支注释原写「不能 await」，不成立
（async generator 在 `aclose()` 里可以 await，不能 yield）。真实理由：关闭可能来自事件循环关停时的
async-generator finalizer，此时 await 一次 DB 写会抛错或被中途取消；独立 task + `RuntimeError` 分支让这条路径安静，
结算本身是幂等 token CAS。行为不变，只改注释。

## 2026-09-10（PR #394 review 第四轮 I-3）— `run_stream(probe_token=)`：流式入口的探测结算

流式入口（NarraMessenger 流式、A2A SSE）现在也认领探测，`run_stream` 与 `run_and_collect` 用同一
接缝结算：流正常结束 → 按 [[run_collector]] `RunErrorTracker` 的结论（与 `collect_run` 同一条
「最后一个错误 + 致命粘滞」规则）调 `settle_probe`；抛异常 → 失败；`CancelledByUser` → 归还；
消费方关闭流（GeneratorExit；可能在 loop 关停 finalizer 里，不内联 await）→ `_spawn_settle` 在独立 task 上归还。无 token 不碰熔断器。
锁：`test_client_probe_settlement.py` 的 `test_a_streamed_*` 与 `test_an_ordinary_stream_never_touches_the_breaker`。

## 2026-09-10（PR #394 review 第四轮 I-1）— `_new_recorder` 带上探测认领

`_new_recorder(inherited_root_run_id, *, agent_id=None, probe_token=None)`；`run_and_collect`
把自己的 `agent_id` / `probe_token` 交进去，recorder 在 run 行 running 时把它绑定为认领者。
recording 关掉时没有 recorder、不绑定，认领只靠 grant 兜底。

## 2026-09-10（PR #394 review C1）— `run_and_collect(probe_token=)`：触发路径的探测结算接缝

bus lane 与 patrol 会认领熔断器的半开探测，但不经 `BackgroundRun`，此前没人结算：死凭据
每个 grant 周期重跑、延迟永不翻倍，修好的凭据回不到 ACTIVE。现在 `run_and_collect` 多一个
显式参数 `probe_token`（不透传给 runtime），在 recorder 把 events 行写成终态**之后**调
[[circuit_breaker]] 的 `settle_probe`：正常返回按 `RunCollection.is_fatal` 判成败，失败时的
error_type/error_message 取 runtime 自己的 error 帧（熔断器分类认得的词表）；抛异常 → 失败
（异常类名 + 文本）；`CancelledByUser` → 无结论归还。宿主 task 被 cancel 的出口不在这里结算，
由调用方出口的 `release_probe` 兜住。无 token 时完全不碰熔断器（这些路径不记普通 streak）。
锁：`tests/agent_runtime/test_client_probe_settlement.py`（死凭据 streak+1 且下次延迟翻倍、
修好即 ACTIVE、抛异常算失败、停止即归还、无 token 不建行）。

**无 token 的 turn 不读结果对象（第三轮 CI 修复，第四轮 review N-2~N-6 更正表述）。** 成功路径的
结算包在 `if probe_token is not None:` 里——不是为了少调一次（`settle_probe` 自身无 token 即
no-op，所以两个异常分支不加守卫），而是让无 token 的 turn 完全不读 `result`：`tests/channel` /
`tests/lark_module` 里替换 `collect_run` 的手写结果替身不是真 `RunCollection`、没有 `is_fatal`，
每个 turn 都读会让它们红（这是测试债，不是为第三方 runtime 留的接缝——`result` 只可能来自
`collect_run`）。锁：`test_an_ordinary_run_does_not_read_its_result`（结果对象任何属性读取即抛）。

## 2026-08-07 — 把触发树交给 recorder

新增 `_inherited_root_run_id(extra_kwargs)`:从 `trigger_extra_data` 读出
trigger 声明的树,传给 `RunRecorder`。两个 recorder 创建点都改了。

`""` 与缺失都归一为 None —— 开启一棵树的 run(用户在房间里发言、job 到点)
传空值,绝不能被读成"一棵叫空字符串的树"。这个 seam 一旦两侧改名,每个被
引发的 run 都会静默变成孤儿而没有任何测试会红,所以单独钉了测试。

## 2026-07-31 (二次) — run_stream 补 finally 兜底网（review Minor #3）

宿主 task 在生成器挂起点被 cancel 抛 CancelledError（BaseException，
不进 except Exception）——原来这条路径靠周期清扫兜（~90s+60s）。补与
run_and_collect 对称的 finally `_spawn_finalize(failed)`；用
`finalize_deferred` 旗标防它与 GeneratorExit 分支的 deferred finalize
双重竞速（spawn 是 task，finally 执行瞬间 recorder.state 还没翻终态，
仅看 state 会双开两个不同终态的 finalize）。

## 2026-07-31 — 咽喉点挂载 RunRecorder：所有 trigger run 可观察

`run_and_collect` / `run_stream` 都在此挂 [[run_recorder]]：装饰器
`_RecordedRuntime` 包住 runtime（yield 原 typed message，旁路喂
recorder 规范化 dict —— collect_run 零改动），完成/取消/异常在 client
层映射 finalize 终态；宿主 task 被掐（部署重启）走 `_spawn_finalize`
兜底（GeneratorExit 展开中不可 await，规避 lesson #2 的静默 GC）。
recorder 建不出来（杀开关 NARRANEXUS_RUN_RECORDING_DISABLED / DB 不可
得）时 run 照常裸跑 —— 观察者绝不挡 run。lark_trigger 的遗留直调
collect_run 也已并入本 seam（顺带补上 admission 闸门）。未来
HttpAgentRuntimeClient 落地时 recorder 跟传输走（server 侧），trigger
仍然零改动。测试：tests/agent_runtime/test_client_recording.py。

## 2026-07-02 — `silent=True` opt-in flows through the extra_kwargs seam

No signature change. Both `run_and_collect` and `run_stream` already
forward `**extra_kwargs` verbatim to `AgentRuntime.run` (via
`collect_run` for the collect case, direct for streaming), so newer
opt-ins like `silent=True` (skip step_3; memory-only writes; see
[[agent_runtime.py]] silent-mode note) reach the runtime without a
protocol bump. The module docstring now names this behaviour
explicitly so triggers know they can pass `silent=True` as a plain
kwarg. Locked by `tests/agent_runtime/test_silent_mode.py` — the
kwarg propagation test would break loudly if any future filter
in the client silently dropped it.

## Why it exists

`AgentRuntimeClient` — the single seam every trigger uses to run an agent
instead of constructing `AgentRuntime` directly. Goal: route all
in-process agent execution through one interface so (a) the transport can
later become HTTP to a remote agent-runtime service (control-plane /
data-plane split, binding rule #20) and (b) cross-cutting policy
(concurrency admission) lives in one place.

- `AgentRuntimeClient` (Protocol): `run_and_collect` (drive to completion →
  `RunCollection`) + `run_stream` (yield events live).
- `InProcessAgentRuntimeClient`: behaviour-identical to the old
  `collect_run(AgentRuntime(), …)` / `AgentRuntime().run(…)` calls, now
  wrapped by the two-level admission gate (`admission.get_admission_controller().slot(user_id)`)
  — no-op locally, enforced in cloud (rule #14: queues start, never kills).
- `get_agent_runtime_client()` factory — InProcess today; HTTP transport
  to the extracted agent-runtime service is the future swap (only this
  function changes, no trigger does).

## Gotchas

- `run_stream` is an **async generator function** (so the admission slot
  is held for the stream's lifetime via `async with`). Callers still just
  `async for ... in client.run_stream(...)` — identical usage.
- Lazy imports inside the methods avoid the channel/__init__ ↔ AgentRuntime
  circular import; safe to import the client at any trigger's top level.
- Migrated callers: `channel_trigger_base` (lark/slack/telegram),
  `job_trigger`, `message_bus_trigger`, `chat_trigger` (collect + A2A SSE).
  The backend WS path uses `BackgroundRun` directly, not this client (so
  it bypasses the admission gate for now — see admission.py.md).
