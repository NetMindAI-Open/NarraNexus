---
code_file: src/xyz_agent_context/utils/schema_registry.py
last_verified: 2026-07-22
stub: false
---

## 2026-07-22 — bus_agent_activity

Added `bus_agent_activity` (composite PK agent_id+channel_id) — a lightweight live-status
mirror for team-room agent runs (state/phase/tool_count/heartbeat). Written by the trigger,
read by the team-chat status view. See [[_bus_activity]].

## 2026-07-21 — teams.lead_agent_id

Added nullable `lead_agent_id VARCHAR(64)` to `teams` — the agent that answers a team-chat
message with no @mention (NULL = earliest-joined member fallback). auto_migrate adds it
idempotently. See [[teams]].

## 2026-07-20 — bus_messages.attachments

Added nullable `attachments TEXT` to `bus_messages` (JSON list of bus-attachment
dicts). `auto_migrate()` back-fills the column idempotently; no destructive change.
See [[_bus_attachment_impl]] for the multimodal-A2A feature it backs.

## 2026-07-22 — 计费可审计化：cost_records +user_id/provider_source，新增 quota_deductions

`cost_records` 加两列（additive、nullable）：`user_id`（VARCHAR(128)，对齐
`user_quotas.user_id` 避免 join 截断）+ `provider_source`（VARCHAR(32)），并加
`idx_cost_records_user_id`。此前只能靠 `agent_id → agents.created_by` 追用户，
agent 硬删即断链。新表 `quota_deductions`（逐笔扣减流水，自审计冗余
provider_source/model/agent_id）：`user_quotas` 只有累计标量，无法定位/退还单笔
错扣。写入见 [[cost_tracker]] / [[quota_repository]]；历史回填见
`scripts/backfill_cost_records_user_id.py`。
## 2026-07-21 — team_catalog 表(Team Marketplace)

additive:catalog INDEX,一行一个 team/agent bundle 模板;store_key 指向
artifact store 里的 .nxbundle(独立 prefix),bundle_sha256 防篡改。unique
template_id + (enabled, sort_order) 索引。cloud registry 写,desktop 空表。


## 2026-07-21 — skill_catalog.is_default 列(stage 9)

additive TINYINT(1) 默认 0:标记「建 agent 时自动安装」的默认技能。
manifest 的 `"default": true` 在 publish 时写入。


## 2026-07-20 — Skill Marketplace: 4 new tables

Registered `skill_catalog` (cloud-authoritative marketplace directory, one
row per skill_id × version, UNIQUE(skill_id, version)), `skill_installations`
(per-workspace audit follower of the filesystem skill state,
UNIQUE(agent_id, user_id, skill_id) — note the triple: workspaces are
`{agent_id}_{user_id}`), `skill_scan_results` (append-only scan runs,
non-unique (skill_id, version) index on purpose — latest row by id wins),
and `team_skill_policies` (placeholder; Team Recommended phase adds logic).

All four exist (empty) on desktop deployments because auto_migrate is
unconditional; only the cloud instance writes catalog/scan rows. Purely
additive. Spec:
`reference/self_notebook/specs/2026-07-20-skill-marketplace-tech-design-v1.1.md` §3.

## 2026-07-16 — user_providers 加 netmind_account_id / netmind_account_email

两列 additive、nullable:铸 NetMind key 时(netmind_provisioner)捕获的账户身份
(user_system_code + email),供 Settings 显示"该充哪个账户"。非 NetMind 行与旧行留 NULL。
非密——绝不存登录 JWT。见 `netmind_provisioner.py.md` 与
`.mindflow/project/references/netmind_billing.md`。

## 2026-07-15 — MCP 管道改名 `mcp_urls`/`mcp_server_urls` → `mcp_servers`

值类型从 url 字符串升级为 spec 对象 `{"url": str, "headers": {str:str}?}`，
支撑用户 MCP 自定义请求头（Authorization 等）贯穿全链路。本文件仅机械跟随
改名/类型，职责不变。

## 2026-07-13 — Agent 实时层熔断器接入

注册新表 `instance_agent_circuit_breaker`（实时层 Agent 熔断状态，键 agent_id，双方言，additive auto_migrate 落为新表）。列：cb_status/consecutive_failure_count/failure_category/cooldown_until/paused_reason/paused_at/last_error/时间戳。


## 2026-07-09 — agent_slots (per-agent LLM slot overrides)

New table ``agent_slots`` (registered right after ``user_slots``), mirroring
``user_slots`` column-for-column but keyed by ``agent_id``. A row here overrides
the owner's ``user_slots`` for that slot on runs of THIS agent only; absence =
inherit the user default. Both ``agent`` and ``helper_llm`` slots may be
overridden (helper follows its agent). Identical column vocabulary is deliberate:
``resolver._apply_agent_overrides`` overlays a row onto ``by_slot_name`` and the
existing card-lookup / self-heal / driver-dispatch consumes it unchanged. Unique
index ``(agent_id, slot_name)`` + ``(agent_id)``. Additive migration only.

## 2026-06-10 — user_slots.params_json column

`user_slots` gained a nullable `params_json` (TEXT/MEDIUMTEXT) column: one
extensible JSON object for framework-neutral per-slot params (currently
thinking + reasoning_effort; future per-slot knobs reuse it without another
migration). NULL = all params auto. Purely additive — auto_migrate() adds
it on next startup of every process.


## 2026-06-09 — embedding subsystem removed → ORPHANED ZOMBIE data (cleanup DEFERRED)

The unified-memory refactor dropped the entire embedding/RAG subsystem
(retrieval is now BM25 + grep, see [[record]] "No embeddings anywhere"). This
registry therefore NO LONGER declares:

- **whole tables**: `embeddings_store`, `chat_message_embeddings`,
  `instance_rag_store`
- **columns on shared/active tables**: `narratives.routing_embedding` /
  `embedding_updated_at` / `events_since_last_embedding_update`,
  `events.event_embedding` / `embedding_text`, `*.capability_embedding`, etc.

`auto_migrate` is **additive-only** (it iterates the REGISTRY and does
CREATE/ADD/INDEX IF NOT EXISTS; it never enumerates the live DB to DROP
extras — binding rule #6). So on every **already-deployed** database (cloud
MySQL + local `run.sh`/DMG SQLite) those tables and columns **remain in place
as orphaned zombie data**. This is intentional and safe:

- no live code path reads/writes them (verified: zero embedding-table refs +
  zero deleted-module imports across `src/` + `backend/`);
- every dropped column on an active table was nullable or carried a DEFAULT
  (the only NOT NULL one, `events_since_last_embedding_update`, had
  `default=0`), so new code's INSERTs (which omit them) are never rejected.

Cost: a little disk, zero functional impact. **DEFERRED (buffering):** a future
explicit, idempotent cleanup migration (`mNNNN`: `DROP TABLE IF EXISTS
embeddings_store / chat_message_embeddings / instance_rag_store`, drop the dead
columns) should run through the versioned `migrations/` ledger — NOT through
`auto_migrate` — so the destructive step is audited, run-once, and Owner-
authorized (rules #6/#12). Not done in this release on purpose.

## 2026-06-09 — schema_migrations ledger table

Added the `schema_migrations` TableDef (migration_id PK / applied_at /
app_version / notes) — the run-once ledger for the versioned data-migration
runner (see migrations/ [[__init__]]). `auto_migrate` creates it like any other
table, so the runner (which fires right after auto_migrate at startup) can
read/write it.

## 2026-06-08 — source_ref column + MEMORY_KINDS

The `memory_<kind>` table definition (`_memory_kind_table`) gained an additive `source_ref` column (TEXT/JSON) for the projection pointer. `MEMORY_KINDS` enumerates the memory kinds (event/narrative/chat/entity/bus/job/observation) used by account-deletion and bundle paths. `instance_social_entities` TableDef is KEPT (bundle round-trip builds a fresh DB via auto_migrate and still needs it), but no live code path writes it any more — entities live in `memory_entity` (see [[social_network_repository]]).

## 2026-06-08 — user_settings table (analytics opt-out)

New table `user_settings` — per-user flat-column preferences. First consumer:
`analytics_opt_out` (TINYINT(1), default 0). A missing row means "not opted
out" — read path in `UserSettingsRepository.is_analytics_opted_out` returns
`False` when no row exists. Insert-or-update pattern in
`set_analytics_opt_out`: single `get_one` + branch on existence; `updated_at`
is not updated in-band because `db.update` uses parameterized placeholders
(the raw SQL expression `(datetime('now'))` would be stored as literal text).
New columns can be added via the registry as new preferences appear —
`auto_migrate` is additive.

## 2026-06-17 — user_slots.agent_framework column

`user_slots` 新增 nullable `agent_framework`（TEXT/VARCHAR(32)，DDL 默认
`'claude_code'`）。只在 `slot_name='agent'` 那一行有意义，驱动
`step_3_agent_loop` 的 SDK 分发：`"claude_code"` → ClaudeAgentSDK，
`"codex_cli"` → CodexSDK。带默认值是为了让已存在的旧行无需单独 backfill 就向后兼容
——resolver 同样把 null 当作 claude_code 处理。纯 additive，`auto_migrate` 下次启动
自动 `ALTER TABLE ADD COLUMN`。

## 2026-06-11 — invite_codes table marked retired (data kept)

Table definition stays so existing rows survive: they hold the only old-user-id -> email mapping needed by scripts/migrate_users_to_netmind.py. No code writes the table anymore; safe to drop after migration completes.

## 2026-06-10 — user_slots.params_json column

`user_slots` gained a nullable `params_json` (TEXT/MEDIUMTEXT) column: one
extensible JSON object for framework-neutral per-slot params (currently
thinking + reasoning_effort; future per-slot knobs reuse it without another
migration). NULL = all params auto. Purely additive — auto_migrate() adds
it on next startup of every process.


## 2026-06-09 — embedding subsystem removed → ORPHANED ZOMBIE data (cleanup DEFERRED)

The unified-memory refactor dropped the entire embedding/RAG subsystem
(retrieval is now BM25 + grep, see [[record]] "No embeddings anywhere"). This
registry therefore NO LONGER declares:

- **whole tables**: `embeddings_store`, `chat_message_embeddings`,
  `instance_rag_store`
- **columns on shared/active tables**: `narratives.routing_embedding` /
  `embedding_updated_at` / `events_since_last_embedding_update`,
  `events.event_embedding` / `embedding_text`, `*.capability_embedding`, etc.

`auto_migrate` is **additive-only** (it iterates the REGISTRY and does
CREATE/ADD/INDEX IF NOT EXISTS; it never enumerates the live DB to DROP
extras — binding rule #6). So on every **already-deployed** database (cloud
MySQL + local `run.sh`/DMG SQLite) those tables and columns **remain in place
as orphaned zombie data**. This is intentional and safe:

- no live code path reads/writes them (verified: zero embedding-table refs +
  zero deleted-module imports across `src/` + `backend/`);
- every dropped column on an active table was nullable or carried a DEFAULT
  (the only NOT NULL one, `events_since_last_embedding_update`, had
  `default=0`), so new code's INSERTs (which omit them) are never rejected.

Cost: a little disk, zero functional impact. **DEFERRED (buffering):** a future
explicit, idempotent cleanup migration (`mNNNN`: `DROP TABLE IF EXISTS
embeddings_store / chat_message_embeddings / instance_rag_store`, drop the dead
columns) should run through the versioned `migrations/` ledger — NOT through
`auto_migrate` — so the destructive step is audited, run-once, and Owner-
authorized (rules #6/#12). Not done in this release on purpose.

## 2026-06-09 — schema_migrations ledger table

Added the `schema_migrations` TableDef (migration_id PK / applied_at /
app_version / notes) — the run-once ledger for the versioned data-migration
runner (see migrations/ [[__init__]]). `auto_migrate` creates it like any other
table, so the runner (which fires right after auto_migrate at startup) can
read/write it.

## 2026-06-08 — source_ref column + MEMORY_KINDS

The `memory_<kind>` table definition (`_memory_kind_table`) gained an additive `source_ref` column (TEXT/JSON) for the projection pointer. `MEMORY_KINDS` enumerates the memory kinds (event/narrative/chat/entity/bus/job/observation) used by account-deletion and bundle paths. `instance_social_entities` TableDef is KEPT (bundle round-trip builds a fresh DB via auto_migrate and still needs it), but no live code path writes it any more — entities live in `memory_entity` (see [[social_network_repository]]).

## 2026-06-08 — user_settings table (analytics opt-out)

New table `user_settings` — per-user flat-column preferences. First consumer:
`analytics_opt_out` (TINYINT(1), default 0). A missing row means "not opted
out" — read path in `UserSettingsRepository.is_analytics_opted_out` returns
`False` when no row exists. Insert-or-update pattern in
`set_analytics_opt_out`: single `get_one` + branch on existence; `updated_at`
is not updated in-band because `db.update` uses parameterized placeholders
(the raw SQL expression `(datetime('now'))` would be stored as literal text).
New columns can be added via the registry as new preferences appear —
`auto_migrate` is additive.

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

## 2026-07-21 — instance_artifact_versions no longer registered

The dead `instance_artifact_versions` TableDef was removed from the registry.
Safe unilaterally: auto_migrate never drops, so existing databases keep the
table and rows untouched (hand-migration of old saved HTML still possible);
fresh databases simply stop provisioning a table no code has read or written
since 2026-05-14. `instance_artifacts.latest_version` stays registered — a
column removal only matters together with the destructive DROP migration,
which remains one Owner-gated batch (铁律 #6); see the cleanup TODO.

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
