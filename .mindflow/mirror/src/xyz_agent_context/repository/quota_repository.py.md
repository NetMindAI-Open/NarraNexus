---
code_file: src/xyz_agent_context/repository/quota_repository.py
stub: false
last_verified: 2026-04-16
---

# Intent

Pure DB I/O for `user_quotas`. Business rules (enable/disable gating,
staff grant vs automatic initialisation, cloud-mode no-op semantics) live
in QuotaService; this layer is deliberately dumb.

## Upstream
- QuotaService (agent_framework/quota_service.py) — only caller
- Tests (tests/repository/test_quota_repository.py) — directly drives atomic
  SQL concurrency assertions

## Downstream
- AsyncDatabaseClient (utils/database.py) — raw SQL `execute` + CRUD helpers
- schema_registry `user_quotas` table — row shape

## Design decisions
- `atomic_deduct` / `atomic_grant` use a single SQL UPDATE with no SELECT
  beforehand. A read-modify-write pattern would race under concurrent LLM
  requests from the same user and silently lose counts.
- Status transitions (`active` → `exhausted`, `exhausted` → `active`) are
  computed inside the same UPDATE via a SQL CASE expression, keeping the
  whole transition atomic.
- Placeholder style is `%s` to match the rest of the project's raw-SQL
  repositories (user_repository.py). AsyncDatabaseClient translates to
  `?` when the backend is SQLite via `_mysql_to_sqlite_sql`.

## Gotchas
- `id_field = "user_id"` — the logical key exposed by this repo. The
  physical table PK is the surrogate `id` column (AUTO_INCREMENT). The
  inherited `get_by_id` / `update` / `delete` helpers therefore operate
  on `user_id`, not `id`.
- `_parse_dt` must handle both `datetime` objects (returned by aiomysql
  under MySQL) and ISO strings (returned by aiosqlite), including the
  trailing `Z` form from serialised timestamps.
- Row-level concurrency safety depends on the backend. SQLite serialises
  writes to the file-level write lock; MySQL InnoDB at REPEATABLE READ
  uses row-level locking with index-lookup updates. Both satisfy the
  guarantee this repo assumes.
- `remaining <= 0` in the CASE is intentional: hitting exactly 0 flips
  the user to `exhausted`, not just strictly-negative.
