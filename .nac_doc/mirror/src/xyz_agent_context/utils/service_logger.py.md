# service_logger.py

One-call setup for rotating file logging in background services — wire up a loguru file sink without repeating boilerplate.

## Why it exists

The project runs several long-lived background processes (ModulePoller, MCP server, Matrix trigger, job trigger). Each needs persistent log files with daily rotation and automatic cleanup, written to a well-known path under `~/.narranexus/logs/`. Without a shared utility, every service would repeat the same `logger.add(...)` call with slightly different parameters, leading to inconsistent formats and log paths. `service_logger.py` provides a single `setup_service_logger()` call that each service makes once at startup.

## Upstream / Downstream

**Called by:** `services/module_poller.py`, `backend/main.py` (or the service entry points for MCP, Matrix trigger, job trigger). Each caller passes its own `service_name` string to get a dedicated log directory.

**Depends on:** `loguru` and stdlib `pathlib`. No other application modules.

## Design decisions

**`~/.narranexus/logs/<service_name>/` as the log root.** Using a fixed path under the user's home directory rather than a project-relative path means logs are findable regardless of the working directory — important when services are launched from different directories (dev server vs. packaged app vs. Tauri sidecar).

**Daily rotation at midnight, 7-day retention, zip compression.** These defaults were chosen to balance disk usage against debuggability: a week of compressed logs is enough to trace most issues while keeping storage bounded.

**`enqueue=True`.** Loguru's async-safe mode prevents the logger from blocking the event loop on slow disk writes. All background services are async, so this is always on.

**Returns the log directory path.** Callers can use the returned `Path` to display or expose the log location in UIs or startup messages.

## Gotchas

**`setup_service_logger` adds a sink, it does not replace the default one.** Loguru's default sink (stderr) remains active. All log lines are written to both stderr and the file. In production, if you want file-only output, you must remove the default sink before calling `setup_service_logger`.

**`LOG_ROOT` is hardcoded to `~/.narranexus/logs/`.** If the product is rebranded or the log path changes, this constant is the only place to update (and all service entry points that call `setup_service_logger` will pick up the change automatically).

**New-contributor trap.** If two services with the same `service_name` call `setup_service_logger`, they will write to the same log file and interleave their output. Service names must be unique across the running process set.
