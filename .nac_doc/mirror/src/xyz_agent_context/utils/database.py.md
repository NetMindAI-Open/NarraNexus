# database.py

`AsyncDatabaseClient` — the single database client every layer of the codebase talks through, plus the MySQL-to-SQLite dialect translator.

## Why it exists

`database.py` is the project's central database client. Every piece of code that needs to read or write data — repositories, modules, background services, MCP tools — communicates through `AsyncDatabaseClient`. A second critical responsibility lives here: `_mysql_to_sqlite_sql()` rewrites MySQL-flavored queries (backticks, `%s`, `ON DUPLICATE KEY UPDATE`, `NOW()`, etc.) before they reach a SQLite backend, letting all callers write MySQL syntax regardless of deployment environment. Keeping the translator here rather than inside the SQLite backend is intentional: `sqlite_proxy_server.py` also imports it directly to apply the same translation to HTTP-proxied raw SQL.

## Upstream / Downstream

**Receives from:** `settings.py` — `load_db_config()` and `settings.database_url` drive both the connection parameters and the backend-selection branch. `schema_registry.TABLES` is queried by `_get_unique_cols_for_table()` to build `ON CONFLICT(...)` targets for upserts.

**Consumed by:** `db_factory.py` (wraps `create_with_backend()` to produce the process-wide singleton); every class under `repository/` (all Repository subclasses call CRUD methods on the client); `sqlite_proxy_server.py` (re-exports `_mysql_to_sqlite_sql`); `utils/__init__.py` (re-exports `AsyncDatabaseClient` and `DatabaseClient` alias).

## Design decisions

**Backend-delegation pattern.** `AsyncDatabaseClient` originally embedded aiomysql pool logic directly. As SQLite and proxy backends were added, all concrete driver code was pushed into `DatabaseBackend` subclasses; the client now delegates every operation to `self._backend`. The legacy aiomysql pool attributes still exist on the object but in practice every code path reaches a backend.

**Lazy initialization.** `AsyncDatabaseClient()` can be constructed without awaiting anything. The backend is created on the first awaited call in `_ensure_pool()`. This lets module constructors accept a `database_client` parameter without the caller needing to have previously awaited anything.

**`_owns_backend` flag.** When a client auto-switches to share the factory singleton's backend (the `url.startswith('sqlite')` branch in `_ensure_pool`), it sets `_owns_backend = False`. Calling `.close()` on such a client does nothing to the shared backend — only the factory's `close_db_client()` tears it down.

**`aiomysql` is always imported.** Even in a pure SQLite deployment, `aiomysql` must be installed because `aiomysql.Pool` appears in the class's type annotations and attribute defaults. This is a known rough edge: the package is conditionally unused at runtime but required at import time.

**`_mysql_to_sqlite_sql` is a module-level function, not a method.** This keeps it importable by `sqlite_proxy_server.py` without creating any instance.

## Gotchas

**Reserved-word columns without backticks.** The translator turns backticks into double-quotes, but columns whose names are MySQL reserved words (e.g., `trigger`, `key`) that appear unquoted in a raw SQL string are passed through unchanged. In SQLite they are treated as bare identifiers and produce `sqlite3.OperationalError: no such column` rather than a syntax error.

**`ON DUPLICATE KEY UPDATE` with unregistered tables.** `_get_unique_cols_for_table()` looks up the unique-index columns in `schema_registry.TABLES`. If the table is not registered there, it falls back to `[table_name]` as the conflict target — which is virtually always wrong. Upserts silently become plain inserts. Any table that needs upsert support must appear in the registry.

**Event-loop change after in-process restart.** `_ensure_pool` delegates to the factory singleton for SQLite URLs. Any `AsyncDatabaseClient` instance that has already cached `self._backend` holds a reference to the old event loop's backend. After a loop change those instances raise `aiosqlite` "Event loop is closed" errors. Always obtain the client via `await get_db_client()` rather than storing it as a long-lived instance attribute.

**New-contributor trap.** Calling `AsyncDatabaseClient()` — no `await` — looks like it returns a ready client, and in many cases it works fine due to lazy init. But if the first call made on it fails (e.g., missing `DATABASE_URL`), the error surfaces as a cryptic connection failure at the first awaited operation, not at construction time.
