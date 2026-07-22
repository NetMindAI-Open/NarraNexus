---
code_file: src/xyz_agent_context/repository/quota_repository.py
stub: false
last_verified: 2026-07-18
---

## 2026-07-18 — 列语义重定义：偏好 → 耗尽通知闩锁

`prefer_system_override` 不再是用户偏好（免费额度优先=平台行为，见
[[provider_resolver]]）：1=armed（下次耗尽要发通知）、0=fired（本轮已发）。
`set_preference`/`disable_if_enabled` 的 SQL 与 CAS 机制原样保留（下条），
只有语义换了；repo 方法名沿用列名不改。

## 2026-07-07 — `disable_if_enabled` compare-and-swap (#48)

`UPDATE … SET prefer_system_override=0 WHERE user_id=%s AND
prefer_system_override=1`, returning `rowcount>0`. The `WHERE prefer=1` guard
makes it a CAS: under concurrent exhausted requests exactly one caller sees the
row still ON and flips it, so exactly one gets True — the single owner of the
one-time auto-switch notice. Relies on `execute(fetch=False)` returning the
affected-row count.

# Intent

Pure DB I/O for `user_quotas`. Business rules (enable/disable gating,
staff grant vs automatic initialisation, cloud-mode no-op semantics) live
in QuotaService; this layer is deliberately dumb.

## Upstream
- QuotaService (agent_framework/quota_service.py) — only caller
- Tests (tests/repository/test_quota_repository.py) — SQLite-backed
  atomic concurrency assertions
- Tests (tests/repository/test_quota_repository_mysql_underflow.py) —
  MySQL-only regression guards for the UNSIGNED-underflow bug. Enabled
  by `NARRANEXUS_MYSQL_TEST_URL`; SQLite cannot reproduce the defect.

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
- **Additive comparisons, never subtractive.** The CASE conditions are
  written as `used + delta >= cap` (deduct) and `used < cap + delta`
  (grant) so every operand on each side of the comparison is a sum of
  UNSIGNED values. A subtractive form like `cap - used - delta <= 0`
  underflows BIGINT UNSIGNED the moment the user overshoots the budget,
  which MySQL rejects with error 1690 and rolls the whole UPDATE back —
  freezing `used` at the boundary and leaving `status='active'` forever
  (see bug fix 2026-04-23). SQLite does not surface this because its
  INTEGER is signed, which is why the SQLite tests did not catch it.
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
- `used + delta >= cap` in the CASE is intentional: hitting exactly the
  cap flips the user to `exhausted`, not only strictly-over.
- `atomic_deduct` is permitted to push `used` past the cap (one "last
  straw" LLM call may over-consume by its cost). This is by design — the
  next `check()` sees `remaining_input = max(0, cap - used) = 0`, which
  returns `False`, which lets auth_middleware raise the proper 402 /
  `SystemDefaultUnavailable` UX. The overshoot is bounded by a single
  request's token cost, not by time.
