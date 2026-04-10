# sqlite_proxy_server.py

Standalone FastAPI HTTP service that exclusively owns the SQLite file — the server side of the multi-process SQLite access architecture.

## Why it exists

SQLite allows only one writer at a time, enforced by a file lock. When the Tauri desktop app runs four separate processes that all write to the same SQLite file, they collide. `sqlite_proxy_server.py` solves this by being the one and only process that holds an open `aiosqlite` connection to the database file. All other processes (FastAPI backend, MCP server, ModulePoller) reach the database exclusively through HTTP calls to this proxy. Because the proxy is single-process and aiosqlite serializes writes internally with an `asyncio.Lock`, file lock contention disappears entirely.

## Upstream / Downstream

**Started by:** `run.sh` and the Tauri sidecar, before any other service, because other services block in `SQLiteProxyBackend.initialize()` until `/health` returns 200.

**Imports from the application:** `_mysql_to_sqlite_sql` from `database.py` (applies dialect translation to raw SQL forwarded via `/execute`), `SQLiteBackend` from `db_backend_sqlite.py` (the actual driver), and `detect_backend_type` / `parse_sqlite_url` from `db_factory.py`.

**Called by:** `SQLiteProxyBackend` (from `db_backend_sqlite_proxy.py`) in every other process that needs database access.

**Reads from environment:** `DATABASE_URL` (must be a `sqlite://` URL) and `SQLITE_PROXY_PORT` (default 8100).

## Design decisions

**FastAPI for the HTTP layer.** The proxy exposes one POST endpoint per `DatabaseBackend` method: `/execute`, `/execute_write`, `/get`, `/get_one`, `/get_by_ids`, `/insert`, `/update`, `/delete`, `/upsert`, and `/transaction/*`. Pydantic request/response models are used for each, matching the parameter shapes of the `DatabaseBackend` ABC. This means the proxy's API surface is exactly as wide as the backend interface — no more, no less.

**Applies `_mysql_to_sqlite_sql` on `/execute`.** Raw SQL forwarded through `/execute` may contain MySQL syntax (callers write MySQL-flavored queries throughout the codebase). The proxy applies the same translation layer that `AsyncDatabaseClient.execute()` would apply, ensuring consistency regardless of whether a client is local or remote.

**Single `SQLiteBackend` instance in the lifespan context.** The `@asynccontextmanager` lifespan creates one `SQLiteBackend` at startup and tears it down at shutdown. This means the proxy holds exactly one connection, with WAL mode and the asyncio write lock, for the entire service lifetime.

**No authentication.** The proxy listens only on `localhost` and assumes that processes on the same machine are trusted. Exposing the proxy on a non-loopback interface would be a security risk, as any HTTP client could execute arbitrary SQL.

## Gotchas

**Must start before all other services.** If the proxy is not running when other services start, those services block for up to ~40 seconds in `SQLiteProxyBackend.initialize()` before failing with `ConnectionError`. The startup order in `run.sh` and the Tauri sidecar must guarantee the proxy is first.

**`DATABASE_URL` must be a `sqlite://` URL.** The proxy validates this at startup via `detect_backend_type`. Passing a MySQL URL will raise `ValueError` and the proxy will refuse to start. There is exactly one right configuration: `sqlite:///path/to/db`.

**SQL translation happens at the proxy, not only at the client.** `SQLiteProxyBackend` on the client side already translates SQL before sending it. The proxy also translates on `/execute`. This means raw SQL sent directly to the proxy (e.g., via `curl`) will be translated, which is the intended behavior but can be confusing when debugging.

**New-contributor trap.** The proxy's Pydantic request models (`GetRequest`, `InsertRequest`, etc.) mirror the `DatabaseBackend` method signatures but are not the same objects. Changes to `DatabaseBackend`'s method signatures must be reflected in both `db_backend_sqlite_proxy.py` (client side) and `sqlite_proxy_server.py` (server side) simultaneously.
