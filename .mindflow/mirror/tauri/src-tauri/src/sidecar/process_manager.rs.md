---
code_file: tauri/src-tauri/src/sidecar/process_manager.rs
last_verified: 2026-07-22
---

## 2026-07-22 — required-service list follows the worker consolidation

`is_required_service` now matches `sqlite_proxy | backend | mcp | workers` (was
`… | poller | job_trigger | message_bus_trigger`). The four worker processes
collapsed into the single `workers` supervisor ([[run_worker_supervisor.py]] /
[[state.rs]]), so the portless-worker liveness sweep on startup now checks `mcp`
+ `workers`. An unconfigured IM channel never fails `workers` (per-channel
startup is isolated inside `start_channel_triggers`); the supervisor only fails
on a real bug (import / DB unreachable), which the startup grace window catches.
No runtime restart logic was added here — the supervisor does its OWN per-task
backoff-restart; this file's contract is still startup-gating only.

The portless liveness sweep also now **promotes** each alive portless service
`Starting → Running` (via `promote_to_running`). Port-bound services get promoted
by `verify_port_ready`, but portless ones (`mcp`, `workers`) had no port to gate
on, so they sat at `Starting` forever — the System page showed a permanent
yellow "启动中" for MCP/Workers even while every worker ran. (Pre-existing;
surfaced by the consolidated card.)

## 2026-07-13 — forward NetMind ("Power") env to the backend sidecar

The backend `cmd` block now forwards the Power-login env vars
(`NARRANEXUS_ENABLE_POWER_LOGIN`, `NETMIND_USE_SUBSCRIPTION_ENABLED`,
`NETMIND_AUTH_API_URL`, `BILLING_API_BASE`, `NETMIND_KEY_API_BASE`,
`NETMIND_INFERENCE_BASE`) to the sidecar. A Finder-launched .app inherits no
shell env, so — mirroring the existing `NARRA_POSTHOG_KEY` `option_env!` line
right above — each is baked at COMPILE time (build shell exported it) with a
runtime env override taking precedence. Unset at build → None → not forwarded →
the packaged app stays pure-local (username-only); this is the DMG twin of the
backend `is_power_login_enabled()` gate. **`scripts/build-desktop.sh` is
intentionally NOT modified** — the frontend half is baked by exporting
`VITE_ENABLE_POWER_LOGIN` before that script (vite picks up exported `VITE_*`),
and cargo reads these six via `option_env!` in the same build shell. See
[[deployment_mode]] for the axis design.

## 2026-05-22 — post-spawn readiness gate + detailed failure

`start_all` no longer just spawns and hopes. After each port-bound service
(sqlite_proxy :8100, backend :8000) it calls `verify_port_ready` — polls the
port (TCP connect; backend gets 45s for first-run DB migration, others 20s) and
fails fast if the child exits during the wait. Required PORTLESS workers (mcp,
poller, job_trigger, message_bus_trigger — see `is_required_service`) get a
final liveness sweep (`try_wait`) to catch crash-on-startup. A required service
failing returns a detailed `startup_error` (label + reason + log path + stderr
tail); optional channel triggers (lark/slack/telegram) only warn. `lib.rs` turns
that error into a native dialog instead of the old silent `log::error!` that
left the UI to fail with "Connection failed". Iron rule #7: `scripts/dev-local.sh`
runs the equivalent checks for the headless path. Borrow note: read the child's
exit status into an owned `Option<String>` BEFORE calling `self.startup_error`
(can't hold a `&mut self.processes` borrow across the `&self` call).

The log drainer (`spawn_log_drainer`) also mirrors every sidecar line to THIS
process's own stdout/stderr (`println!`/`eprintln!`), in addition to the buffer
+ per-service file. So launching the app from a terminal streams the full live
sidecar log (startup crashes, port-bind errors, tracebacks); a Finder launch
sends them to the system log (Console.app).

# process_manager.rs — Child process lifecycle manager with log collection

Manages the six Python sidecar processes. Core responsibilities:

1. **Spawn** (`start_service`): `tokio::process::Command` with explicit env
   vars, piped stdio, `kill_on_drop`.
2. **Drain pipes** (`spawn_log_drainer`): detached tokio tasks that read
   stdout/stderr line-by-line and **fan out to two destinations**:
   - the in-memory `VecDeque<LogEntry>` ring buffer (500 entries/service,
     consumed by the `get_logs` Tauri command for the live LogViewer)
   - an append-only file at
     `$NEXUS_LOG_DIR/<service_id>/<service_id>_YYYYMMDD.log`
     (defaults to `~/.narranexus/logs/`, mirrors what the Python
     `setup_logging()` writes when running headless via
     `bash run.sh`). Daily rollover is implicit — the path is
     recomputed per line and reopened on date change. Any I/O error
     suppresses only the on-disk copy; the ring buffer keeps working.
3. **Start all in order** (`start_all`): sorts by `ServiceDef.order`, applies
   per-service `startup_delay_ms` between starts.
4. **Stop / restart** (`stop_service`, `stop_all`, `restart_service`):
   graceful — sends `libc::SIGTERM` first, waits up to 3s for the child
   to exit on its own, falls back to `child.kill()` (SIGKILL) only if
   the timeout elapses. SIGKILL-only was the historical behavior and
   left ports lingering in TIME_WAIT across relaunches because Python
   couldn't run its `finally` / `await trigger.stop()` paths.
5. **Query** (`get_all_status`, `get_logs`): read-only access to status map
   and log buffer.

## The log drainer is critical — read this

Python services write to stderr via loguru. If nothing reads the pipe, the
Linux/macOS kernel buffer fills (~16 KB) and the child **blocks on its next
write** — a silent deadlock. In practice this manifested as the agent loop
hanging at step 3.2 in the packaged `.dmg` (the first chat always triggered
enough log output to fill the buffer). Fixed in commit `5cf8c1d`.

`spawn_log_drainer` spawns a `tokio::spawn` task per pipe. The task loops
on `lines.next_line().await` and on each line:

1. appends a `LogEntry` to the in-memory ring buffer (capped at
   `max_logs`, oldest dropped first);
2. appends a one-line text record to the daily file at
   `~/.narranexus/logs/<service>/<service>_YYYYMMDD.log` so the
   desktop run mode keeps the same on-disk layout as headless
   `bash run.sh` (ironclad rule #7).

The file is opened lazily and reopened on day rollover. The directory
is created lazily once at task start (`tokio::fs::create_dir_all`).
File-side I/O errors degrade to ring-buffer-only: the in-memory copy
keeps working so the live LogViewer is never silenced by a bad write
or a quota-full disk. The task exits naturally on EOF (child closed
the fd).

## The two mutex types

```rust
type LogBuffer = Arc<StdMutex<VecDeque<LogEntry>>>;
// vs.
pub process_manager: Arc<tokio::sync::Mutex<ProcessManager>>;
```

Log appends use `std::sync::Mutex` (not async-aware) because the drainer
tasks never cross an `.await` point while holding the lock. This keeps log
writes decoupled from the outer async mutex, preventing potential deadlocks if
`start_all` holds the outer lock while drainers want to push logs.

## Explicit env var propagation

```rust
let db_url = std::env::var("DATABASE_URL").unwrap_or_default();
let proxy_url = std::env::var("SQLITE_PROXY_URL").unwrap_or_default();
// ...
Command::new(...).env("DATABASE_URL", &db_url).env("SQLITE_PROXY_URL", &proxy_url)
```

`std::env::set_var` in `lib.rs::setup()` is not thread-safe on macOS. The
tokio thread that calls `start_service` may not see the write. Explicit
`.env()` bypasses the inheritance path. Without `SQLITE_PROXY_URL` the Python
side opens SQLite directly, causing multi-process lock contention.

For the `backend` service only, two extra env vars are injected: a static
`NARRA_SURFACE=desktop` (analytics surface label), and — via
`option_env!("NARRA_POSTHOG_KEY")` — a `POSTHOG_API_KEY` forwarded to the
Python sidecar. `option_env!` reads at COMPILE time: official release builds
get the key baked in (CI sets the secret), while community/source builds
resolve to `None` and ship no key, so the backend stays on NullSink. This is
the single mechanism that makes telemetry "official builds only".

The `backend` service additionally gets `.env("NARRA_SURFACE", "desktop")` so
the Python `analytics.surface.resolve_surface()` knows it runs the desktop
surface. The local launcher (`scripts/dev-local.sh`) injects `local`, container
mode (`run.sh` `run_container_mode`) injects `cloud`; this is the desktop one of
those three launch-path labels.

## PATH injection for bundled Node.js CLIs

`start_service` prepends `state::resolve_bundled_node_bins()` to the PATH
handed to each child. Without this, `claude_agent_sdk` (Python) spawns the
`claude` binary via `shutil.which`, which under a Finder-launched `.app`
only sees the launchd minimal PATH (`/usr/bin:/bin:/usr/sbin:/sbin`) and
fails every chat turn.

Dev mode returns an empty path list → parent PATH is preserved unchanged.

## Upstream / downstream

- **Upstream:** `ServiceDef` from `state.rs`
- **Used by:** `commands/service.rs` (IPC), `lib.rs` (auto-start, shutdown)
