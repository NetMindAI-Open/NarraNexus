---
code_file: src/xyz_agent_context/agent_framework/remote_agent_loop_driver.py
stub: false
last_verified: 2026-06-17
---

## Why it exists

`RemoteAgentLoopDriver` — the network transport behind the step-3
`AgentLoopDriver` seam. Same `agent_loop(...)` async-generator contract
as the local claude/codex drivers, but instead of spawning the CLI
in-process it POSTs to the Executor service and streams the raw event
dicts back. This is the mirror of `HttpAgentRuntimeClient`, one layer
down (the control-plane side of binding rule #20's split).

## Selection / behaviour

- Chosen by `get_agent_loop_driver` when `AGENT_EXECUTOR_URL` is set
  (cloud orchestrator). Unset → local in-process driver, so `bash run.sh`
  and the desktop build are unchanged (binding rule #7).
- Ships the scoped provider configs in the request body
  (`executor_protocol.build_agent_loop_request` snapshots them) because
  they normally ride a ContextVar that does not survive the hop.
- Long-run safe (binding rule #14): `aiohttp` timeout `total=None`,
  `sock_read=None` — gaps between events during long tool calls must not
  abort the stream.
- Re-raises on the executor's `{"error": ...}` line so step-3's except
  path captures it exactly as a local-driver exception.

## Gotcha (burned once, 2026-06-17)

`CancellationToken.is_cancelled` is a **bool `@property`, not a method**.
The first draft called it `()` → `TypeError: 'bool' object is not
callable`, which aborted runs at the first event. Read it, do not call
it. Regression test:
`tests/agent_runtime/test_executor_seam.py::test_remote_driver_honours_cancellation_property`.
