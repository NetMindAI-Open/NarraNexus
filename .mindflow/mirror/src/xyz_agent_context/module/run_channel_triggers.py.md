---
code_file: src/xyz_agent_context/module/run_channel_triggers.py
stub: false
last_verified: 2026-07-09
---

## Why it exists

The consolidated supervisor for ALL IM channel triggers. Replaces the six
near-identical `run_<channel>_trigger.py` entrypoints (deleted 2026-07-08) with
one process running every `ChannelTriggerBase` subclass in a single event loop.

Motivation (see `reference/self_notebook/specs/2026-07-08-trigger-consolidation-design.md`):
- Memory: the heavy package import graph was resident six times; now once.
- SQLite: six processes each opened the same file, multiplying lock contention
  (handoff issue #5); one process = one opener.
- Maintenance: the six-process fact was hard-coded across run.sh, dev-local.sh,
  deploy-cloud.sh, and the Tauri desktop factories.

## Design decisions

- **`start_channel_triggers(db, only, trigger_map)` is the testable core.** It is
  extracted from `main()` (which owns DB acquisition + the infinite keepalive
  loop) so unit tests exercise selection / isolation / pre_start ordering with a
  fake map and no DB. Tests: `tests/channel/test_channel_supervisor.py`.
- **Per-channel startup isolation.** Each channel's instantiate → `pre_start` →
  `start` is wrapped in try/except; one channel failing never aborts the others.
  This is the supervisor's replacement for the process-level isolation lost by
  consolidating (the base class already isolates per-task failures at runtime).
- **`--only lark,slack` selects a subset.** Lets cloud split a high-volume
  channel into its own container with zero code change. Default (empty) = all.
- **Relies on `ChannelTriggerBase.start()` being non-blocking.** `start()` spawns
  the credential watcher + workers as asyncio tasks and returns, so N triggers
  coexist in one loop. The `while True: sleep(1)` only keeps the process alive.
- **`pre_start` hook** runs each channel's one-off migration (e.g. Lark's legacy
  `auth_status`) before `start`, keeping channel-specific logic in the channel
  (rule #4) instead of the shared entrypoint.

## Upstream / downstream

- **Upstream**: launched by run.sh / dev-local.sh / .dev-local-safe.sh /
  deploy-cloud.sh / Tauri `state.rs` (both factories).
- **Downstream**: `CHANNEL_TRIGGER_MAP`, each trigger's `pre_start`/`start`/`stop`,
  and `channel_health_server.start_channel_health_server` (one aggregated
  /healthz for all channels).

## Gotchas

Three shutdown hazards caught by the 2026-07-08 end-to-end run (all fixed):

- **The supervisor MUST own signal handling.** The aggregated health server's
  uvicorn installs its own SIGINT handler by default and swallows Ctrl+C, so the
  supervisor tells uvicorn `install_signal_handlers = lambda: None` and installs
  its own `loop.add_signal_handler` for BOTH SIGINT and SIGTERM. Shutdown waits
  on an `asyncio.Event`, not a `while True` loop.
- **SIGTERM must be handled explicitly.** `asyncio.run()` handles SIGINT but NOT
  SIGTERM; without an explicit handler, systemd/`docker stop` would hard-kill the
  process instead of letting each channel `stop()`.
- **The DB client MUST be closed on shutdown.** The aiosqlite backend runs its
  single connection on a background thread that keeps the process alive after
  `main()` returns — skipping `close_db_client()` turns a clean signal into a
  hang. The old per-channel entrypoints never closed it but exited via a
  propagating `KeyboardInterrupt`; the supervisor returns normally, so it must
  close explicitly.
- `loguru.complete()` is drained inside the same `asyncio.run` scope on shutdown;
  a fresh `asyncio.run` would bind it to a closed loop (inherited from the old
  per-channel entrypoints).
- Out of scope: `JobTrigger` and `MessageBusTrigger` are NOT `ChannelTriggerBase`
  subclasses and keep their own processes.
