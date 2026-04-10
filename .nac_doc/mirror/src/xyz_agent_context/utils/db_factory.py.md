# db_factory.py

Process-wide singleton for `AsyncDatabaseClient` — resolves the backend from the URL and hands the same connection to every caller.

## Why it exists

The original codebase had 40+ direct `DatabaseClient()` calls, each creating its own connection. MCP tool handlers were especially problematic: the Agent runtime cannot inject a db client into MCP tools at call time, so every tool call would open a new connection. This exhausted MySQL connection limits and created uncontrollable resource leaks. `db_factory.py` provides a single `get_db_client()` coroutine that returns the same `AsyncDatabaseClient` instance for the lifetime of the process. It is also the only place that reads `settings.database_url` to decide which backend to instantiate, and it houses the URL-parsing utilities (`detect_backend_type`, `parse_sqlite_url`) that other parts of the codebase need.

## Upstream / Downstream

**Reads from:** `settings.py` (via `settings.database_url`) and the `SQLITE_PROXY_URL` environment variable.

**Instantiates:** `SQLiteBackend` (from `db_backend_sqlite.py`), `MySQLBackend` (from `db_backend_mysql.py`), or `SQLiteProxyBackend` (from `db_backend_sqlite_proxy.py`), then wraps the chosen backend in `AsyncDatabaseClient.create_with_backend()`.

**Consumed by:** `database.py` (`_ensure_pool` delegates to `get_db_client()` when auto-switching to SQLite); `utils/__init__.py` (re-exports `get_db_client`, `get_db_client_sync`, `close_db_client`); every MCP tool handler and background service that calls `await get_db_client()`.

## Design decisions

**URL-scheme-based backend selection.** The decision tree is: if `database_url` starts with `sqlite://` and `SQLITE_PROXY_URL` is set, use `SQLiteProxyBackend`; if `sqlite://` without proxy, use `SQLiteBackend`; otherwise fall back to `MySQLBackend` with `load_db_config()`. This keeps all environment-detection logic in one file.

**Double-checked locking with `asyncio.Lock`.** The singleton is initialized inside an `async with _initialization_lock` block and the condition is re-tested after acquiring the lock to handle the case where two concurrent callers both see `_shared_async_client is None`.

**Event-loop tracking.** `_client_event_loop` stores the loop that was running when the singleton was created. If `get_db_client()` is called from a different loop (e.g., a test runner that recreates the loop), it discards the old client and builds a fresh one. The old backend is not explicitly closed because it is tied to the old loop; closing it would require scheduling work on that loop, which may already be closed.

**`get_db_client_sync` is an escape hatch, not the primary path.** It is provided for code that cannot be made async (e.g., top-level synchronous scripts). If called from within a running event loop it raises `RuntimeError` immediately rather than deadlocking.

**`detect_backend_type` and `parse_sqlite_url` are module-level utilities** that are also imported by `database.py`'s lazy-init path and by `sqlite_proxy_server.py`. Keeping them here rather than in `database.py` avoids a circular import (the factory imports from `database.py`, not the other way around).

## Gotchas

**Multiple processes do not share the singleton.** Each Python process gets its own `_shared_async_client`. The FastAPI backend, MCP server, and ModulePoller each have their own independent connection. Under SQLite this means concurrent writes from different processes fight over the file lock — that is the exact problem `sqlite_proxy_server.py` was built to solve. Set `SQLITE_PROXY_URL` to consolidate all DB writes through the proxy.

**`close_db_client()` must be awaited at shutdown.** The shared backend holds an open file handle (SQLite) or connection pool (MySQL). Forgetting to await `close_db_client()` on process exit leads to "database is locked" errors if another process tries to open the same SQLite file immediately after.

**Changing `DATABASE_URL` mid-process has no effect.** The factory reads `settings.database_url` only on the first call. Subsequent calls return the cached singleton even if the environment variable has been updated. Restart the process to pick up a new URL.

**New-contributor trap.** Calling `get_db_client_sync()` before any event loop has been started (e.g., in a module-level initializer) spins up a temporary event loop via `asyncio.run()`. That loop is torn down immediately, leaving the `_shared_async_client` backend attached to a closed loop. The next async call that tries to use the singleton will fail with "Event loop is closed."
