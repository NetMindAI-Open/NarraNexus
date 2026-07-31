---
code_file: src/xyz_agent_context/agent_runtime/client.py
stub: false
last_verified: 2026-07-31
---

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
