# db_backend_mysql.py

`MySQLBackend` — the `DatabaseBackend` implementation for cloud/server deployments, using an `aiomysql` connection pool.

## Why it exists

When the database layer was refactored to support pluggable backends, the MySQL-specific driver code (pool management, `%s` placeholders, backtick quoting, `ON DUPLICATE KEY UPDATE`) was extracted from `AsyncDatabaseClient` into `MySQLBackend`. This allows `AsyncDatabaseClient` to stay dialect-agnostic and lets `db_factory.py` select the backend based solely on the URL scheme. `MySQLBackend` is the backend for all production cloud deployments where `DATABASE_URL` does not start with `sqlite://`.

## Upstream / Downstream

**Instantiated by:** `db_factory.py` for MySQL URLs; also `database.py`'s `_ensure_pool` lazy-init path when auto-detecting MySQL.

**Implements:** `DatabaseBackend` (from `db_backend.py`).

**Depends on:** `aiomysql` for the connection pool and cursor operations.

## Design decisions

**`aiomysql.create_pool` for concurrency.** Unlike SQLite's single connection, MySQL supports many simultaneous connections. The pool size and recycle interval are configurable at construction time and default to 10 connections, 1-hour recycle. The pool is created at `initialize()`, not at construction, so the class can be instantiated synchronously.

**`%s` placeholders, backtick-quoted identifiers.** MySQL uses `%s` for parameters and backticks for identifiers. All identifier strings passed to `get`, `insert`, etc. are validated by `_validate_identifier` (alphanumeric + underscore) and then backtick-quoted to avoid reserved-word collisions.

**`INSERT ... ON DUPLICATE KEY UPDATE ... AS new_row` for upserts.** The `upsert` method generates MySQL 8.0.20+ syntax using an alias (`new_row`) rather than the deprecated `VALUES()` function. This is more explicit and future-proof, but means the code will fail on MySQL versions older than 8.0.20.

**Transaction support via a dedicated connection.** Transactions use a single connection acquired from the pool and stored as `self._transaction_connection`. Concurrent calls to transaction methods on the same backend instance are not safe; callers are expected to use one backend instance per async task for transactional work, or to wrap operations in the higher-level `asynccontextmanager` provided by `AsyncDatabaseClient`.

**Value serialization mirrors `SQLiteBackend`.** `_serialize_value` converts `bool` to `0/1`, `datetime` to ISO 8601 strings, and `dict/list` to JSON strings. This ensures the two backends produce compatible stored representations so data written by MySQL can be read back under SQLite (and vice versa for the proxy path).

**IS NULL handling.** `get`, `update`, and `delete` filter clauses detect `None` values and generate `IS NULL` SQL instead of `= NULL`, which would always be false in MySQL.

## Gotchas

**MySQL 8.0.20+ upsert syntax.** The `INSERT ... AS new_row ON DUPLICATE KEY UPDATE new_row.col = ...` syntax requires MySQL 8.0.20 or later. Older MySQL versions reject this syntax with a parse error. If you need to support older MySQL, the `upsert` method needs modification to use the deprecated `VALUES(col)` form.

**Pool exhaustion under high concurrency.** The default pool size is 10. Long-running transactions or slow queries can hold connections, causing other coroutines to block waiting for a connection. Symptom: operations start timing out even though MySQL is healthy. Check `pool_size` against the expected concurrency.

**`_validate_identifier` rejects legitimate names with hyphens.** Column or table names containing hyphens (e.g., from external systems) will raise `ValueError` from `_validate_identifier`. This is intentional for SQL-injection prevention but can be surprising if you expect the validator to be lenient.

**New-contributor trap.** `aiomysql` cursors return tuples by default. `MySQLBackend` sets `cursorclass=aiomysql.DictCursor` to get dict rows. If you bypass the backend and use the raw pool directly, you will get tuples unless you explicitly pass the cursor class.
