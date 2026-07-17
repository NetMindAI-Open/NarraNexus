---
code_file: src/xyz_agent_context/utils/db_factory.py
last_verified: 2026-07-13
stub: false
---

## 2026-05-22 — parse_sqlite_url collapses leading `//`

Callers build the URL as `sqlite:///` + an already-absolute path → the
SQLAlchemy 4-slash absolute form `sqlite:////Users/...`. `parse_sqlite_url`
stripped only `sqlite://` (2 slashes), leaving a malformed `//Users/...` (macOS
tolerates it but it showed up as `database: //Users/...` in proxy logs). It now
collapses a leading run of slashes to one; `:memory:` / relative paths untouched.

# db_factory.py

Per-event-loop registry for `AsyncDatabaseClient` — resolves the backend
from the URL and hands each asyncio event loop its own pool.

## Why it exists

The original codebase had 40+ direct `DatabaseClient()` constructions, each
creating its own connection. MCP tool handlers were the worst case: the
agent runtime cannot inject a db client into MCP tools at call time, so
every tool call would open a new connection. `db_factory.py` centralises
that: one `get_db_client()` coroutine is the only way to acquire a client,
and it owns the backend-selection logic (`detect_backend_type`,
`parse_sqlite_url`).

**Why per-loop, not per-process** (2026-04-22 rewrite): the MCP container
runs every module in its own `threading.Thread` + `asyncio.new_event_loop`
via `module_runner.run_mcp_servers_async`. That means 8 concurrent asyncio
loops share one Python process. aiomysql's internal Futures (e.g.
`Pool._wakeup`) bind to the loop that created the pool; reusing the pool
from another loop raises "got Future attached to a different loop" — the
exact error that blew up `mcp__job_module__job_create` in production on
2026-04-22. The previous "singleton + recreate on loop change" design only
displaced the problem (whichever loop lost the race held stale Futures).

## Upstream / Downstream

**Reads from:** `settings.py` (via `settings.database_url`) and the
`SQLITE_PROXY_URL` environment variable.

**Instantiates:** `SQLiteBackend` (from `db_backend_sqlite.py`),
`MySQLBackend` (from `db_backend_mysql.py`), or `SQLiteProxyBackend`
(from `db_backend_sqlite_proxy.py`), wrapped by
`AsyncDatabaseClient.create_with_backend()`.

**Consumed by:** `database.py` (lazy-init auto-switch to SQLite);
`utils/__init__.py` (re-exports `get_db_client`, `get_db_client_sync`,
`close_db_client`); every MCP tool handler and background service.

## Design decisions

**Per-loop dict keyed by `id(loop)`.** `_clients_by_loop` maps
`id(running_loop) → AsyncDatabaseClient`. `_loops_by_id` keeps a strong
reference so we can detect closed loops; `_locks_by_loop` keeps a
per-loop `asyncio.Lock` so first-call races within a single loop are
serialised without cross-loop lock binding (a `asyncio.Lock()`
constructed while a given loop is running binds to that loop).

**Cheap eviction on every access.** `_evict_closed_loops()` iterates
`_loops_by_id` and drops any entry whose `loop.is_closed()` returned
true. This is O(n) in the number of loops the process has ever held
(typically < 10), runs before the hot-path lookup, and guards against
`id()` collisions when a new loop object is allocated at the same
memory address as a dead one.

**`close_db_client()` dispatches closes back to the origin loop.**
Closing a pool from a different loop than the one it was built on would
reintroduce the exact cross-loop bug we're fixing. When
`close_db_client()` runs from outside the origin loop, it uses
`asyncio.run_coroutine_threadsafe(client.close(), loop).result(timeout=5)`.
If the origin loop is already closed, the client is closed on the
CURRENT loop instead (2026-07-13). The earlier "drop the entry, process
exit reclaims it" assumption was wrong for the SQLite backend: aiosqlite
runs a NON-daemon worker thread per connection, so an unclosed client
blocks interpreter shutdown forever — this was the "pytest prints its
summary then hangs" bug. Cross-loop close is safe precisely because the
origin loop is gone: aiosqlite's close() only needs *a* running loop.

**`SYNC_KEY = -1` pseudo-loop-id for the sync bootstrap path.**
`get_db_client_sync()` is kept as an escape hatch for code paths that run
before any asyncio loop exists (top-level synchronous scripts). The
client it returns was built via a throwaway `asyncio.run()` loop and
**must not** be reused from async contexts — its pool is bound to a
loop that has already been torn down. Caching under `SYNC_KEY` merely
prevents `asyncio.run()` from being invoked twice for the same process.

**URL-scheme-based backend selection.** Decision tree: `sqlite://` +
`SQLITE_PROXY_URL` set → `SQLiteProxyBackend`; `sqlite://` alone →
`SQLiteBackend`; everything else → `MySQLBackend` with `load_db_config()`.
All environment-detection logic lives in this one file.

**`detect_backend_type` / `parse_sqlite_url` are module-level utilities.**
Also imported by `database.py`'s lazy-init path and by
`sqlite_proxy_server.py`. Keeping them here rather than in `database.py`
avoids a circular import (the factory imports from `database.py`, not
the reverse).

## Gotchas

**Multiple processes still do not share a pool.** The FastAPI backend,
MCP server, ModulePoller, job trigger, bus trigger, and Lark trigger
each run as separate Docker services; each gets its own per-loop
registry in its own process memory. Under SQLite this means concurrent
writes from different processes contend for the file lock — that is the
problem `sqlite_proxy_server.py` exists to solve; set
`SQLITE_PROXY_URL` to serialise all DB writes through the proxy.

**RDS connection budget scales with loops, not processes.** Each loop
builds a fresh aiomysql pool. On the MCP container alone that's 4
active modules × `pool_size` (default 10) = 40 connections. Add the
other 5 Python services at 10 each = another 50. Total ~90 idle
connections steady-state, more under burst. Confirm
`max_connections` on the RDS cluster is comfortably above this before
scaling out further.

**`close_db_client()` from the wrong thread hangs briefly.** It uses
`fut.result(timeout=5)` to bound the wait; after 5 s the close is
abandoned and the client entry is cleared anyway. Don't rely on
close_db_client for ordering against other shutdown tasks.

**Changing `DATABASE_URL` mid-process has no effect.** The factory reads
`settings.database_url` only when a loop first requests a client.
Subsequent calls return the cached per-loop singleton.

**`get_db_client_sync()` returns a dead-loop client.** Calling it and
then trying to use the result from an async context will fail with
"Event loop is closed." That's by design — the sync path exists for
sync callers only. Newer code should never need it.

## Historical context

- 2026-04-21 (`0aec35d`): removed `XYZBaseModule._mcp_db_client`
  class-level cache that shadowed the factory's loop-change detection.
  Necessary prerequisite but not sufficient — the factory was still a
  process-wide singleton that thrashed under multi-loop MCP.
- 2026-04-22 (this commit): factory itself becomes per-loop. See
  TODO-2026-04-22 R1 / BUG_FIX_LOG Bug 34 for full debug trail.
