# db_backend_sqlite_proxy.py

`SQLiteProxyBackend` — a `DatabaseBackend` that forwards every operation over HTTP to `sqlite_proxy_server.py`, eliminating multi-process SQLite file lock contention.

## Why it exists

When the Tauri desktop app runs, four separate processes (FastAPI backend, MCP server, ModulePoller, and the Tauri sidecar) all need database access. SQLite allows only one writer at a time and uses a file lock, so concurrent writes from independent processes produce `sqlite3.OperationalError: database is locked` errors — a problem that in-process write serialization (the `asyncio.Lock` in `SQLiteBackend`) cannot solve because each process has its own lock. The proxy architecture resolves this: one dedicated process (`sqlite_proxy_server.py`) holds the exclusive SQLite connection, and all other processes send their DB operations as HTTP requests to that proxy. `SQLiteProxyBackend` is the client side of that architecture — it implements `DatabaseBackend` by translating every CRUD call into an HTTP POST to the appropriate proxy endpoint.

## Upstream / Downstream

**Instantiated by:** `db_factory.py` when `database_url` starts with `sqlite://` and the `SQLITE_PROXY_URL` environment variable is set (e.g., `http://localhost:8100`).

**Calls:** `sqlite_proxy_server.py` endpoints: `/execute`, `/execute_write`, `/get`, `/get_one`, `/get_by_ids`, `/insert`, `/update`, `/delete`, `/upsert`, `/transaction/*`, and `/health` for readiness checks.

**Implements:** `DatabaseBackend` (from `db_backend.py`), so `AsyncDatabaseClient` uses it transparently.

**Depends on:** `httpx` for the async HTTP client.

## Design decisions

**HTTP as the IPC mechanism.** A shared memory queue, Unix domain socket, or named pipe were considered as alternatives. HTTP was chosen because FastAPI already runs in the proxy process, `httpx` provides a mature async client with connection pooling, and the REST interface is trivially inspectable with `curl` for debugging.

**Exponential backoff on `initialize()`.** The proxy process may not be ready when other processes start. `initialize()` retries the `/health` check up to 15 times with waits capped at 5 seconds, rather than failing immediately. This makes startup ordering less fragile.

**`dialect` returns `"sqlite"` and `placeholder` returns `"?"`.** The backend reports its dialect as SQLite so that `AsyncDatabaseClient.execute()` still applies `_mysql_to_sqlite_sql` translation before forwarding raw SQL. The proxy server then receives already-translated SQLite SQL.

**Connection pool capped at 20.** `httpx.AsyncClient` is configured with `max_connections=20`, matching the expected concurrency from the FastAPI process's async task pool.

**Token-threaded transactions, scoped by ContextVar.** `begin_transaction`, `commit`, and `rollback` are forwarded as separate HTTP calls but are NOT independent: `begin_transaction` captures the server-issued `txn_id`, and every subsequent write (`insert`/`update`/`delete`/`upsert`/`execute_write`/`execute`) carries that token so the proxy admits the holder's own writes and blocks everyone else's until the transaction ends. `commit`/`rollback` send the token and clear it in a `finally`.

The token lives in a module-level `contextvars.ContextVar` (`_current_txn`), NOT on the backend instance. This is the subtle, load-bearing part: `db_factory` hands ONE `AsyncDatabaseClient`/`SQLiteProxyBackend` to every coroutine on an event loop, so an instance attribute would be shared by all concurrent requests in that process — a transaction opened by one (e.g. `wipe_service`) would stamp its token onto every OTHER coroutine's writes, and the proxy would fold them into that transaction (same-process P1). A ContextVar is copied per asyncio Task, so only the task that called `begin_transaction` (and coroutines it awaits) carries the token; independent request tasks see `None` and are correctly gated. No change is needed in `AsyncDatabaseClient` or callers. Reads (`get`/`get_one`/`get_by_ids`) are not gated. Residual: a child task explicitly spawned by the transaction-holder task after `begin` inherits the context (and thus the token) — narrow and arguably in-scope, unlike the shared-instance leak which hit every concurrent request.

## Gotchas

**`SQLITE_PROXY_URL` must be set before any process calls `get_db_client()`.** The factory reads the env var once at initialization. A process that starts before the env var is injected will use `SQLiteBackend` (direct file access) instead, defeating the proxy's purpose and reintroducing lock contention.

**HTTP timeout defaults to 30 seconds.** A slow query (e.g., a full table scan on a large dataset) that takes longer than 30 seconds will cause `httpx.ReadTimeout`. The default can be overridden at construction, but the proxy server itself has no query timeout — a long query just holds the connection.

**Proxy must start before other services.** If `sqlite_proxy_server.py` is not running, `initialize()` waits ~40 seconds in total (15 attempts with exponential backoff) before raising `ConnectionError`. The startup orchestrator (`run.sh` and the Tauri sidecar) must launch the proxy first.

**New-contributor trap.** The `dialect` property returns `"sqlite"` even though this backend communicates over HTTP. This surprises people who expect a network-transport backend to have a different dialect. The dialect is `"sqlite"` because the proxy server runs SQLite and expects SQLite-syntax SQL; the transport layer is irrelevant to the dialect.
