---
code_file: tauri/src-tauri/src/sidecar/process_manager.rs
last_verified: 2026-04-10
---

# process_manager.rs — Child process lifecycle manager with log collection

Manages the six Python sidecar processes. Core responsibilities:

1. **Spawn** (`start_service`): `tokio::process::Command` with explicit env
   vars, piped stdio, `kill_on_drop`.
2. **Drain pipes** (`spawn_log_drainer`): detached tokio tasks that read
   stdout/stderr line-by-line into a `VecDeque<LogEntry>` ring buffer.
3. **Start all in order** (`start_all`): sorts by `ServiceDef.order`, applies
   per-service `startup_delay_ms` between starts.
4. **Stop / restart** (`stop_service`, `stop_all`, `restart_service`): sends
   SIGKILL via `child.kill()`.
5. **Query** (`get_all_status`, `get_logs`): read-only access to status map
   and log buffer.

## The log drainer is critical — read this

Python services write to stderr via loguru. If nothing reads the pipe, the
Linux/macOS kernel buffer fills (~16 KB) and the child **blocks on its next
write** — a silent deadlock. In practice this manifested as the agent loop
hanging at step 3.2 in the packaged `.dmg` (the first chat always triggered
enough log output to fill the buffer). Fixed in commit `5cf8c1d`.

`spawn_log_drainer` spawns a `tokio::spawn` task per pipe. The task loops on
`lines.next_line().await`, appending to the shared ring buffer. It exits
naturally on EOF (child closed the fd).

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

## Upstream / downstream

- **Upstream:** `ServiceDef` from `state.rs`
- **Used by:** `commands/service.rs` (IPC), `lib.rs` (auto-start, shutdown)
