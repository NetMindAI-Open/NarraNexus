---
code_file: src/xyz_agent_context/utils/schema_registry.py
last_verified: 2026-04-28
stub: false
---

## 2026-04-28 addition — chat_message_embeddings folded in

Registered `chat_message_embeddings` here alongside the other
`_register(TableDef(...))` calls. This was the last table in the
codebase still living under the legacy "one create script per table"
model in `utils/database_table_management/`. The script was orphaned —
nothing in the codebase imported it, so every fresh local DB was
missing the table, every ChatModule hook was failing silently with
`no such table: chat_message_embeddings`, and `ChatModule` was
burning embedding API calls every turn for nothing (Bug #1).

The orphan script `create_chat_message_embeddings_table.py` is gone;
new deployments build the table via `auto_migrate()` like every other
table. The whole `utils/database_table_management/` folder no longer
exists.

Reader side stays empty for now: nothing reads from the table yet —
the intended Part B retrieval surface for ChatModule history was
never wired up. Letting the writer succeed silently lets embeddings
accumulate for whatever surface gets built later.

# schema_registry.py

Single source of truth for every database table — define columns once, run on both SQLite and MySQL, migrate automatically.

## Why it exists

Before this file, table schemas lived only as raw `CREATE TABLE` SQL strings in individual `create_*_table.py` scripts, one set per dialect. Columns could drift between environments and there was no programmatic way to detect what needed migrating. `schema_registry.py` centralizes every column and index definition in Python dataclasses. The `auto_migrate` path reads `TABLES` at startup and issues `ALTER TABLE ADD COLUMN` for any column present in the registry but absent from the live database. The registry also feeds `_get_unique_cols_for_table()` in `database.py` when it needs to build `ON CONFLICT(...)` targets for SQLite upsert statements.

## Upstream / Downstream

**Consumed by:**
- `database.py` — `_get_unique_cols_for_table()` reads `TABLES` to resolve conflict columns for `ON DUPLICATE KEY UPDATE` translation.
- `database_table_management/auto_migrate.py` and the `create_*` scripts — iterate `TABLES` to create missing tables and add missing columns.
- Tests and tooling that call `get_registered_tables()` — the public accessor returns `list(TABLES.values())` so callers don't need to import the private `TABLES` dict directly.

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

## 2026-04-21 · v2 时区协议字段

`instance_jobs` 表新增 4 列：`next_run_at_local` / `next_run_tz` / `last_run_at_local` / `last_run_tz`（全部 TEXT/VARCHAR, nullable）。语义见 spec `reference/self_notebook/specs/2026-04-21-job-timezone-redesign-design.md` 第 4.1 节。

这些列是 additive 变更，`auto_migrate` 启动时自动 `ALTER TABLE ADD COLUMN` 即可。**不改**原 `next_run_time` / `last_run_time` 列名或类型（它们在新协议下专职承载 UTC，对 LLM 不可见）。
