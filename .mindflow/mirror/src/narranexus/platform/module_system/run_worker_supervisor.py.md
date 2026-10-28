---
code_file: src/narranexus/platform/module_system/run_worker_supervisor.py
stub: false
last_verified: 2026-09-07
---

## 2026-09-03（批 2b.5）— `run()` 先 `boot_worker_plugins()` 再 `build_specs`

插件 worker 规格来自 `backend.workers` 注册表，必须在选 spec 前登记。

## 2026-09-03（批 2a.4）— 插件 worker 进 supervisor

`build_specs(..., registries=)` 在四个内置之后追加 `backend.workers` 里 `host="workers"` 的贡献，
名字 `<owner>:<name>`（`--only/--exclude` 用全名）；契约 `WorkerSpec.factory` 返回的句柄适配成本模块的
`WorkerHandle`。spec 工厂抛错只跳过该插件。无插件时列表与 `workers.json` 快照完全不变。

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
see [[admin/runtime.py]] — has data within a tick of boot and ≤30 s staleness)
carrying a per-worker liveness snapshot
(`{name: {state, restart_count, last_error}}`, `state ∈ starting/running/
restarting/stopped`) to the `service_audit` table.

**Read that snapshot as L1, not L2.** `set_state(name, "running")` is written
ONCE when `_supervise` starts a worker and is never touched again unless the
worker raises; it proves the asyncio task object exists, not that the worker is
doing anything. On 2026-07-27 the bus worker's poll loop wedged on an await and
this snapshot reported `bus: {"state": "running", "restart_count": 0}` every 30 s
for 33 hours while zero messages moved. Real L2/L3 has to come from each worker's
own auditor and its work counters — poller, jobs and (since 2026-07-28)
[[message_bus_trigger]] each keep one. (Incident lesson #4: L1 alone is a back
door for zombies — this is the door.)

## Upstream / downstream

- **Upstream**: launched by run.sh (container + `exec dev-local.sh`),
  scripts/dev/dev-local.sh, scripts/dev/.dev-local-safe.sh, scripts/release/deploy-cloud.sh, and
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
  (poller 3 + jobs 5 + bus `settings.bus_max_workers` + channels workers).
  Pool size is now env-tunable
  via `MYSQL_POOL_SIZE` (default 10; see [[db_factory.py]]) — a supervisor
  deployment should set ≥25. **2026-08-14 correction**: the "cloud still runs 4
  separate containers, so the single-pool case has not bitten yet" note that
  used to sit here is obsolete — deploy `stacks/narranexus-app/compose.yml` runs
  the `workers` service on `run_worker_supervisor` with `MYSQL_POOL_SIZE: "25"`.
  The single pool is live, so the arithmetic above is load-bearing: the `bus`
  term is `settings.bus_max_workers` (3 → 8 on 2026-08-14), and raising it again
  means revisiting that 25. Found while reviewing that change — the stale note
  had been used as evidence that raising the bus term was free.
- The individual workers keep their `if __name__ == "__main__"` blocks as
  standalone DEBUG entrypoints only — no launcher wires them anymore.

## 2026-09-04 · ingress triggers (batch 3c.3)

`jobs` left `WORKER_SPECS`: the job clock is builtin.job's `ingress.triggers` contribution (host=workers) adapted by `trigger_worker_specs` — builtin owners keep the bare name so `run.sh --exclude jobs,channels` and `ALL_WORKERS` order are unchanged, while disabling builtin.job removes the worker instead of crash-looping a factory that cannot import it. `build_specs` merges platform workers, trigger workers and plugin workers, keeping the canonical order for the ALL_WORKERS names.

## 2026-09-04 · `main(argv)` (batch 6a)

Argument parsing moved into `main(argv=None) -> int` (called by the `xyz_agent_context.module.run_worker_supervisor` shim); package path `narranexus.platform.module_system`.


## 2026-09-07 — marks the plugin boot healthy only once it serves (round-2 🟡-6)

The plugin boot no longer declares itself healthy on its last line; this
entrypoint calls `plugins_boot.mark_host_healthy(<role>)` after the process is
actually serving, which is the only moment that may clear the boot-crash marker
and move the last-known-good registry snapshot. Reaching the boot and then dying
must count as a failed boot.

## 2026-09-07 — is_builtin_id 收编（round-2 P2-I6）

『是否 builtin』只在 contracts.distribution.is_builtin_id 一处判断（BUILTIN_PREFIX 同处）；九处 startswith('builtin.') 副本全部改调它（distribution_scaffold 的保留命名空间检查是另一个判断，未合并）。
