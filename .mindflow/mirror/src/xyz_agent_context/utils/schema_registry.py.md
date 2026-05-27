---
code_file: src/xyz_agent_context/utils/schema_registry.py
last_verified: 2026-05-27
stub: false
---

## 2026-05-27 — instance_jobs.created_at/updated_at NOT NULL + DEFAULT

`instance_jobs` table had `created_at` / `updated_at` columns with no
constraint and no DEFAULT. Some code paths INSERTed rows leaving
those columns NULL, which then crashed `job_trigger`'s pydantic
`JobModel` validation (see [[job_repository]] companion fix). Added
`nullable=False` + `default="(datetime('now'))"` so future INSERTs
can't recreate the bug. `auto_migrate` is additive-only — existing
NULL rows in already-deployed sqlite DBs are handled at the read
boundary by `_row_to_entity` defensively coercing None to
`datetime.now()`.

## 2026-05-14 — artifact pointer model

Spec: `reference/self_notebook/specs/2026-05-14-artifact-pointer-model-design.md`

`instance_artifacts` gains two pointer-model columns: `file_path` (entry file
relative to `base_working_path`, nullable so auto_migrate adds it to existing
DBs without a backfill) and `size_bytes` (recursive size of the artifact root
directory, `NOT NULL DEFAULT 0`).

`instance_artifacts.latest_version` and the whole `instance_artifact_versions`
table are now **DEPRECATED** — versioning was dropped with the pointer model.
Both are kept registered (so auto_migrate keeps provisioning them) purely so
colleagues with old saved HTML can hand-migrate from the old rows. No code reads
or writes them. Cleanup tracked in
`reference/self_notebook/todo/2026-05-14-cleanup-dead-artifact-versions.md`.

## 2026-05-14 addition — invite_codes

New table `invite_codes` — backs the cloud-mode registration gate, replacing
the single global `INVITE_CODE` env var (deleted from `backend/auth.py`).

One row = one unique, single-use code issued to one email. `code` carries a
unique index; `email` / `status` indexes drive idempotent re-requests (same
email → resend existing issued code) and the Mode-B auto-issue cap count
(`status IN (issued, used)` < cap). status flow: `issued → used` (consumed
atomically by `/api/auth/register`), `waitlisted → issued` (admin promote
when the cap is hit), `→ revoked` (admin kill). `email_sent` records whether
the SMTP send actually succeeded so a failed send is visible/re-sendable in
the admin list without blocking `/api/invite/request`.

Purely additive — `auto_migrate` creates it on next startup. Design doc:
`drafts/logs/invite_code_2026_05_14.md`.

## 2026-05-13 addition — Agent Runtime Lifecycle (Phase C)

`events` 表加 7 个 Phase-C 字段：`state` / `started_at` / `last_event_at`
/ `finished_at` / `tool_call_count` / `current_stage` / `error_message`。
**`state` 的 DDL 默认值是 `completed`**——这是给已存在的旧 events 行的兜底，
让它们不被启动期 reconcile 误判成 stale `running`。

新增 `idx_events_state` + `idx_events_agent_state` 两个索引——前者给
reconcile 扫 stale 行用，后者给 `/api/auth/agents` list 加 active_run
字段的 N+1 SELECT 用（实际 endpoint 用了 IN-列表合并成单个 SELECT，但
索引仍是底层优化）。

新增 `event_stream` 表（编号 30.）——per stream-chunk 副表，跟 `events`
1:N 关联。每段 thinking、每个 tool_call、每个 tool_output 一行。
`(event_id, seq)` unique 复合索引让重连时的 replay 按 seq ASC 一次扫
出全部。**永不清**——audit + 历史回看。

数据量估算（Xiong-style 13 min run）：thinking 段约 50 行 + tool 约 80
行（call + output 各 41）+ progress / text_delta 若干 ≈ 200 行/run。
13 万 run/年 ≈ 2600 万行，~25GB——MySQL 无压力。

Spec: `reference/self_notebook/specs/2026-05-13-agent-runtime-lifecycle-and-stream-resilience-design.md` §4.1

## 2026-05-13 addition — Provider Unification (Phase 0)

`user_providers` gains four nullable columns — `driver_type`, `owner_user_id`,
`billing_policy`, `auth_ref` — plus two indexes (`idx_up_driver_type`,
`idx_up_owner`). `user_slots` gains `last_auto_repaired_at` (nullable) used
as the 24h debounce timestamp for the reverse-validation self-heal path.

New table `user_notifications` (29.) — minimal kind+payload+severity row
written by the resolver when it auto-repairs a broken slot. Indexed on
`(user_id, read_at)` for the "unread count" UI query.

Driver inference (`derive_driver_type`) and one-shot backfill live in
`src/xyz_agent_context/agent_framework/provider_driver/backfill.py`. New
deploys get `driver_type` written at `add_provider` time; pre-existing rows
get backfilled on the next backend boot via `auto_migrate` → `backfill_*`
chain in `db_factory.get_db_client`. Both column-add and backfill are
idempotent so re-running causes no drift.

All new columns are nullable on purpose — older `bash run.sh` / desktop
DMG users upgrade with zero schema drama: `auto_migrate` runs the
ALTERs, the backfill fills the values, business code never sees a
null after the first boot. Old columns (`source`, `auth_type`,
`linked_group`, `prefer_system_override`) are untouched.

## 2026-05-09 hardening — I7 idx_artifact_agent_id added

`instance_artifacts` now has a third index `idx_artifact_agent_id` on `["agent_id"]`.
`total_bytes_for_agent` joins `instance_artifact_versions` to `instance_artifacts` on
`artifact_id` and filters by `agent_id`. Without an `agent_id` index the planner may
scan the full `instance_artifacts` table when an agent has many artifacts. The two
existing composite indexes (`idx_artifact_agent_session`, `idx_artifact_agent_pinned`)
cover query patterns with two conditions; the new single-column index covers the quota
aggregation join path.

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

## 2026-05-08 · Agent Artifact Tabs — instance_artifacts + instance_artifact_versions

Two new tables registered as part of the Agent Artifact Tabs feature
(spec: `reference/self_notebook/specs/2026-05-08-agent-artifact-tabs-design.md`).

**`instance_artifacts`** — one row per artifact emitted by the agent (chart,
csv, markdown, html app, png/jpeg/pdf, etc.). Text primary key `artifact_id`
(prefix `art_` + 8 random chars). Tracks `kind`, `title`, `description`,
`pinned` flag, and `latest_version` counter. `agent_id` and `user_id` are
`VARCHAR(128)` (aligned with `instance_jobs`, `module_instances` and other
module-owned tables — the wider width prevents MySQL truncation for IDs that
can exceed 64 chars in some generator configurations). Indexed on
`(agent_id, session_id)` and `(agent_id, pinned)` for the two common query
patterns: "all artifacts in this session" and "pinned artifacts for this agent".

**`instance_artifact_versions`** — append-only version log. Each row stores the
`file_path` to the artifact file on disk and `size_bytes`. The composite unique
index on `(artifact_id, version)` enforces immutability: a given version of a
given artifact cannot be overwritten. The `latest_version` counter in
`instance_artifacts` is bumped on each new version write.

Both tables are purely additive and take effect on next `auto_migrate()` call
(i.e., next app startup).

## 2026-05-08-r2 · original_session_id column added to instance_artifacts

Added a nullable `original_session_id TEXT/VARCHAR(64)` column to
`instance_artifacts`. This stores the `session_id` at the moment the artifact
is pinned, so that `set_pinned(False)` can restore it instead of leaving the
artifact orphaned with `session_id=NULL`. Purely additive — existing rows get
`NULL` (no session to restore; the route layer surfaces a warning per review
Important #1).
