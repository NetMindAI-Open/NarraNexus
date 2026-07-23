---
code_file: src/xyz_agent_context/module/run_worker_supervisor.py
stub: false
last_verified: 2026-07-22
---

## Why it exists

The consolidated supervisor for ALL long-running background workers. Replaces
the old "one OS process per worker" layout — `module_poller`, `job_trigger`,
`message_bus_trigger`, and `run_channel_triggers`, each a separate
`python -m ...` process — with ONE process running every worker inside a single
asyncio event loop.

Motivation (same shape as the 2026-07-08 channel consolidation, one layer up):
- **Memory**: `import xyz_agent_context` costs ~128 MB resident per process
  (measured); four worker processes paid that four times. Now once — the primary
  win, ~400–500 MB on a local/desktop install.
- **SQLite**: four processes each opened the same file, multiplying lock
  contention; one process = one opener = one per-loop pool.
- **Maintenance**: the four-process fact was hard-coded across run.sh,
  dev-local.sh, .dev-local-safe.sh, deploy-cloud.sh, and both Tauri factories.

MCP stays a SEPARATE process on purpose: it is a port-bound SSE server (a
different kind of thing) and already single-process via
[[module_runner.py]]'s `run_mcp_servers_async`.

## Design decisions

- **`run_channel_triggers.main()` is the direct template** for the shutdown
  machinery (own-signal handling for SIGINT+SIGTERM, `close_db_client`, loguru
  drain inside the loop scope). The channels group is itself supervised as ONE
  of the workers here (via `start_channel_triggers`) — so there is a single
  supervisor, not a supervisor-of-supervisors. The channel core stayed where it
  was ([[run_channel_triggers.py]]); this file imports it.
- **Shutdown drains workers CONCURRENTLY** (`_drain_and_close` gathers all
  `_call_stop`s, PR #136 review). `ModulePoller.stop`/`JobTrigger.stop` each
  block up to 30 s on a queue join; serialising would stack to 60 s+ and, under
  a fixed docker/systemd stop grace, SIGKILL later workers before their drain
  starts. Concurrent → total ≈ max(30 s). Shutdown code reads registered stops
  via `SupervisorContext.stops()`, not the private `_stops` field.
- **Per-task backoff-restart, no run-duration cap.** Each worker runs as a
  supervised task; a task that RAISES is a crash → audited + exponential backoff
  (1→2→…→60 s) + restart; a task cancelled during shutdown is NOT restarted.
  There is deliberately **no** cap on how long a worker may block without
  returning — a worker running for hours is HEALTHY, not a hang (binding rule
  #14). The only timeouts are the restart backoff and each worker's own drain.
  This is a strict reliability improvement over the pre-merge layout, where a
  crashed worker process stayed dead until the app restarted (ProcessManager has
  no restart-on-crash).
- **Factory-per-restart.** `WorkerSpec.factory(ctx)` is called once per
  (re)start so each attempt gets a FRESH coroutine (a coroutine cannot be
  awaited twice) and a fresh worker instance (the previous run's internal task
  lists were consumed by its stop/crash).
- **Channels are the shape exception.** `start_channel_triggers` is non-blocking
  (each `ChannelTriggerBase.start()` spawns its own tasks and returns; the base
  isolates per-task crashes internally). The `_channels_factory` therefore wraps
  the group as a coroutine that starts channels + the /healthz server (port
  47831) then blocks on `stop_event`. We do NOT invent a per-channel restart —
  that would fight the channels' own supervision and double-bind 47831.
- **One migration + quota bootstrap up front.** `run()` calls `auto_migrate` and
  `bootstrap_quota_subsystem` once before starting workers. Each worker's
  `start()` still calls its own `auto_migrate` (idempotent) — kept, not
  refactored away, because injecting db to skip it would touch four
  independently-runnable modules for zero correctness gain.
- **Single loop = shared pool (the aiomysql invariant).** Everything runs under
  one `asyncio.run(run())`, so every `get_db_client()` returns the same per-loop
  singleton and the aiomysql pool's futures stay bound to the live loop. Never
  call `get_db_client_sync()` here (see [[module_runner.py]] for the full
  root-cause of "Future attached to a different loop").
- **`--only` / `--exclude` / `--channels`.** Worker subset selection (default =
  all) lets cloud split workers across VMs/containers with no code change;
  `--channels` is an orthogonal subset within the channels worker (same as the
  old `run_channel_triggers --only`). Unknown names warn; an empty set idles on
  `stop_event` rather than exiting (a misconfigured container restarts
  predictably instead of crash-looping).

## L2 observability

`ServiceAuditor("worker_supervisor")` emits started/stopped plus a heartbeat
(emit-first, then every `_HEARTBEAT_INTERVAL`=30 s so the desktop System page's
Workers card — which reads the latest row via `GET /api/admin/runtime/workers`,
see [[admin_runtime.py]] — has data within a tick of boot and ≤30 s staleness)
carrying a per-worker liveness snapshot
(`{name: {state, restart_count, last_error}}`, `state ∈ starting/running/
restarting/stopped`) to the `service_audit` table. This gives one-row-per-minute
L2 across all merged workers — and is `message_bus_trigger`'s FIRST L2 signal
(it never had its own `ServiceAuditor`). Each of poller/jobs keeps its own
`ServiceAuditor` too, so both granularities coexist. (Incident lesson #4.)

## Upstream / downstream

- **Upstream**: launched by run.sh (container + `exec dev-local.sh`),
  scripts/dev-local.sh, scripts/.dev-local-safe.sh, scripts/deploy-cloud.sh, and
  Tauri [[state.rs]] (both factories, service id `workers`, order 3). The
  startup-alignment guard `tests/channel/test_trigger_startup_alignment.py`
  enforces this wiring.
- **Downstream**: `ModulePoller.start` ([[module_poller.py]]), `JobTrigger.start`
  ([[job_trigger.py]]), `MessageBusTrigger.start` + `_get_bus`
  ([[message_bus_trigger.py]]), `start_channel_triggers`
  ([[run_channel_triggers.py]]) + `start_channel_health_server`, and
  `ServiceAuditor` ([[service_audit.py]]).

## Gotchas

- **Blast radius vs 4 processes.** A Python exception is caught + restarted, but
  a process-fatal fault (OOM / segfault / native abort) now takes all workers
  down together — the wrapper cannot restart the interpreter. Operationally,
  systemd/docker restarts the whole supervisor; cloud can `--only`-split across
  hosts. Same trade the channel consolidation already accepted.
- **Shared loop = shared head-of-line blocking.** All workers are fully async
  today, but any future accidental blocking call starves ALL workers, not one.
- **MySQL pool sizing.** Four independent pools became one. SQLite is strictly
  better (single opener); MySQL must be sized for the busiest concurrent mix
  (poller 3 + jobs 5 + bus 3 + channels workers). Pool size is now env-tunable
  via `MYSQL_POOL_SIZE` (default 10; see [[db_factory.py]]) — a supervisor
  deployment should set ≥25. Note: cloud compose still runs the 4 separate
  containers today, so the single-pool case only bites once compose switches to
  the supervisor (tracked in `reference/self_notebook/todo/`).
- The individual workers keep their `if __name__ == "__main__"` blocks as
  standalone DEBUG entrypoints only — no launcher wires them anymore.
