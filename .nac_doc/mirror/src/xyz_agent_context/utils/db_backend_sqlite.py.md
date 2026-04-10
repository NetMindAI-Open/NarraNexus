# db_backend_sqlite.py

`SQLiteBackend` — the `DatabaseBackend` implementation for local/desktop deployments, using `aiosqlite` with WAL mode and a serializing write lock.

## Why it exists

The Tauri desktop migration needed a file-based database that runs without a server process. `db_backend_sqlite.py` provides the concrete SQLite driver that `AsyncDatabaseClient` delegates to when `DATABASE_URL` starts with `sqlite://`. It wraps `aiosqlite` (async SQLite via a thread pool) and adds three layers of application-level concerns that SQLite itself doesn't handle the same way MySQL does: write serialization, automatic timestamp parsing, and value serialization for composite Python types.

## Upstream / Downstream

**Instantiated by:** `db_factory.py` when no `SQLITE_PROXY_URL` is set and the URL scheme is `sqlite`.

**Implements:** `DatabaseBackend` (from `db_backend.py`), so `AsyncDatabaseClient` uses it transparently.

**Depends on:** `aiosqlite`, `db_backend.py` (ABC), stdlib `asyncio` and `json`.

## Design decisions

**Single long-lived connection, not a pool.** SQLite is a file — there is no network round-trip to amortize. A single `aiosqlite.Connection` is opened at `initialize()` and kept for the backend's lifetime. Connection overhead is negligible compared to the overhead of opening a new file handle per query.

**`asyncio.Lock` for write serialization.** SQLite allows only one writer at a time within a process. Rather than relying on SQLite's retry timeout, the backend holds a write lock before executing any `INSERT`, `UPDATE`, `DELETE`, or `UPSERT`. Reads (`SELECT`) bypass the lock to maximize concurrency under WAL mode.

**WAL journal mode.** `PRAGMA journal_mode=WAL` is set at `initialize()`. WAL allows multiple concurrent readers even while a write transaction is in progress, which is essential for the agent pipeline where many coroutines read context data while background services write module state.

**Automatic timestamp parsing in `_auto_parse_row`.** SQLite stores all datetime values as TEXT. Rather than forcing every caller to parse timestamps, the backend converts columns whose names match known suffixes (e.g., `_at`, `_time`, `created_at`) to Python `datetime` objects when rows are returned. The detection is suffix-based, not universal, to avoid false positives on non-timestamp TEXT columns.

**JSON/dict/list serialized to strings.** Python dicts and lists passed to `insert` or `update` are serialized to JSON strings before storage. On read, the backend does not auto-deserialize JSON (unlike timestamps) — callers that store JSON must `json.loads()` the returned string themselves. This asymmetry is intentional: timestamp conversion is safe and universal, but auto-deserializing every TEXT column that looks like JSON would be error-prone.

**`upsert` uses `INSERT OR REPLACE`.** SQLite's `INSERT OR REPLACE` deletes the conflicting row and re-inserts, which resets auto-increment IDs and triggers `ON DELETE` cascades if any exist. An alternative `ON CONFLICT DO UPDATE` approach was not chosen here; callers that care about preserving the row ID should check whether this matters for their table.

## Gotchas

**Timestamp parsing by suffix, not by type.** If a new TEXT column is added whose name ends in `_at` but does not contain a datetime value, `_auto_parse_row` will attempt to parse it and either return a garbled `datetime` or fall back to the raw string. Avoid naming non-timestamp columns with timestamp suffixes.

**Write lock is per-backend-instance, not per-file.** If two `SQLiteBackend` instances are created pointing at the same file path (which should not happen in production because `db_factory.py` enforces a singleton, but can happen in tests), their write locks are independent and they will race. The symptom is `sqlite3.OperationalError: database is locked`. Use the proxy backend in any multi-process setup.

**WAL files accumulate after a crash.** If the process is killed mid-write, the `-wal` and `-shm` sidecar files remain on disk. SQLite handles recovery automatically on the next open, but the presence of these files can confuse backup scripts that copy only the main `.db` file.

**New-contributor trap.** `aiosqlite` runs SQLite in a thread pool under the hood. Calling a synchronous SQLite operation on the `aiosqlite` connection object directly (without `await`) will block the event loop thread. Always use `async with conn.execute(...)` patterns.
