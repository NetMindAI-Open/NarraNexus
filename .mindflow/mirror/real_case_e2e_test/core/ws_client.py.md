---
code_file: real_case_e2e_test/core/ws_client.py
last_verified: 2026-05-13
stub: false
---

# ws_client.py — drives one fresh-run turn against /ws/agent/run

## Why it exists

This is the only place the harness speaks the runtime-message wire
protocol. By containing every WebSocket detail here, the rest of the
framework (transcript, programmatic, semantic, runner) treats one
turn as a value, not a stream.

## Decisions

- **Fresh run only.** Phase A reconnect / replay are reachable via the
  same endpoint but are out of scope for e2e; cases that need them
  would obscure their intent. We exercise the happy fresh-run path
  every single turn.
- **Driver patience, not agent cap.** The per-turn timeout
  (`turn_timeout_seconds`) is the harness's patience for the agent —
  not a fix-via-cap on agent_loop (iron rule #14). When a turn times
  out we record `timed_out=True` and move on; we never call /stop on
  the agent because that would mask cases where the agent is
  legitimately working long.
- **WS close = completion.** We treat both an explicit `run_ended`
  event and a clean WebSocket close as the completion signal. Either
  alone is enough; this gives us tolerance for protocol-level edges
  (close arrives before/after the event).
- **`max_size=None`.** Tool outputs can run into multi-megabyte
  payloads; default websockets max_size would silently truncate.
