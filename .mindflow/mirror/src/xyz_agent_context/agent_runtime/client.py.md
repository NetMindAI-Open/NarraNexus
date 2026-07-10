---
code_file: src/xyz_agent_context/agent_runtime/client.py
stub: false
last_verified: 2026-07-02
---

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
