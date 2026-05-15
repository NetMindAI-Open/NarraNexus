---
code_file: tauri/src-tauri/src/sidecar/process_manager.rs
last_verified: 2026-05-06
---

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
