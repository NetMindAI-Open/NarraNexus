---
code_file: src/xyz_agent_context/agent_runtime/executor_protocol.py
stub: false
last_verified: 2026-06-17
---

## Why it exists

Wire format for the agent-loop Executor boundary. When step-3 (the only
claude/codex spawn site) is extracted into a separate Executor service,
the call that used to be in-process must cross the network. The hard part
is that the **scoped provider credentials normally travel via ContextVar**
(`api_config._claude_ctx/_codex_ctx`, set by the resolver in the
orchestrator) — a ContextVar does NOT survive a network hop. This module
serializes those configs so they cross explicitly.

## Key points

- `serialize_provider_configs()` — orchestrator side; snapshots the
  current task's resolved configs (via `api_config.snapshot_user_config`)
  to plain dicts. `None` entries preserved (reproduce exact ContextVar
  state, e.g. anthropic_helper unset).
- `apply_provider_configs()` — executor side; rebuilds the frozen
  dataclasses and calls `api_config.set_user_config`, so the SDK's
  `to_cli_env` resolves the same scoped key — **without the executor ever
  touching the DB or the resolver** (that's the whole point: executor
  holds no DB creds).
- `build_agent_loop_request()` — the `POST /agent-loop` body. Deliberately
  does NOT serialize `cancellation` (orchestrator cancels by aborting the
  HTTP stream; executor sees client disconnect).
- Lives in the core package (not `backend/`) so both the executor service
  entrypoint and the remote driver import it without a backend dependency.

## Gotcha

Provider config dataclasses are frozen — reconstructed via
`Cls(**dict)`. If a config gains a field, asdict↔kwargs round-trips
automatically; if it gains a non-trivial type, add explicit handling.
