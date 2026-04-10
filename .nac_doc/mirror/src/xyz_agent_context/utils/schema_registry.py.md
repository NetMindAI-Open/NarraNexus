# schema_registry.py

Single source of truth for every database table — define columns once, run on both SQLite and MySQL, migrate automatically.

## Why it exists

Before this file, table schemas lived only as raw `CREATE TABLE` SQL strings in individual `create_*_table.py` scripts, one set per dialect. Columns could drift between environments and there was no programmatic way to detect what needed migrating. `schema_registry.py` centralizes every column and index definition in Python dataclasses. The `auto_migrate` path reads `TABLES` at startup and issues `ALTER TABLE ADD COLUMN` for any column present in the registry but absent from the live database. The registry also feeds `_get_unique_cols_for_table()` in `database.py` when it needs to build `ON CONFLICT(...)` targets for SQLite upsert statements.

## Upstream / Downstream

**Consumed by:**
- `database.py` — `_get_unique_cols_for_table()` reads `TABLES` to resolve conflict columns for `ON DUPLICATE KEY UPDATE` translation.
- `database_table_management/auto_migrate.py` and the `create_*` scripts — iterate `TABLES` to create missing tables and add missing columns.
- Any tooling or test that needs to enumerate the project schema without touching a live database.

**Depends on:** nothing inside the application. Pure-Python dataclasses; the only runtime import is `loguru`.

## Design decisions

**Dual-type columns (`sqlite_type` / `mysql_type`).** Each `Column` carries both `sqlite_type` (TEXT, INTEGER, REAL, BLOB) and `mysql_type` (VARCHAR(64), MEDIUMTEXT, TINYINT(1), etc.). DDL generators pick the appropriate field for their target dialect. This makes the registry the single place to update a type mapping.

**Append-only migration contract.** `auto_migrate` only adds columns — it never drops, renames, or narrows them. Removing a column from the registry has zero effect on the live database. This is intentional: destructive schema changes require a manual DBA operation. Any attempt to auto-drop columns would be a violation of the project's "no dangerous DB mutations" rule.

**`_register()` at module load time.** Table definitions are registered via `_register(table_def)` at the module's top level, not inside a function. Importing this module is enough to populate `TABLES`. Test fixtures that need extra tables can call `_register` after import.

**No ORM, no query builders.** The registry owns the database shape. Pydantic models live separately in `schema/`. `AsyncDatabaseClient` methods take plain Python dicts, not registry objects.

**`TableDef.primary_key` list for composite PKs.** Most tables have a single auto-increment `id` column with `primary_key=True` on the `Column`. Tables with composite primary keys (e.g., `bus_channel_members`) use the `TableDef.primary_key` list field instead. DDL generators must check both.

## Gotchas

**Adding a column does not migrate existing databases automatically.** `auto_migrate` must be explicitly run (`make db-sync`). Forgetting to run it after pulling new code produces `sqlite3.OperationalError: table X has no column named Y` at runtime, which looks like a code bug.

**SQLite `default` values use SQLite syntax.** The `default` field stores a SQLite expression — e.g., `"(datetime('now'))"` not `"CURRENT_TIMESTAMP(6)"`. MySQL DDL generators must translate these. Copying a default value from a MySQL script verbatim will cause SQLite to reject the `CREATE TABLE`.

**JSON columns are TEXT in SQLite.** Columns with `mysql_type = "JSON"` carry `sqlite_type = "TEXT"`. SQLite's `json_extract` works on TEXT, but MySQL's JSON type enforcement does not apply. Malformed JSON written from application code will be stored without error.

**Upserts need the table registered.** `database.py` falls back to `[table_name]` as the conflict target if the table is not in `TABLES`. An unregistered table that receives an upsert call will silently insert duplicates instead of updating.

**New-contributor trap.** Registering a table here is necessary but not sufficient for a first-time install. The corresponding `create_*_table.py` script must also exist, because `auto_migrate` only adds columns to tables that already exist. A freshly cloned repo with no tables gets nothing from the registry alone.
