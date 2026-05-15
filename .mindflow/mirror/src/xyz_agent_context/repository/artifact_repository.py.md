---
code_file: src/xyz_agent_context/repository/artifact_repository.py
last_verified: 2026-05-14
stub: false
---

## 2026-05-14 — pointer model: version table dropped

Spec: `reference/self_notebook/specs/2026-05-14-artifact-pointer-model-design.md`

The repository no longer touches `instance_artifact_versions`. Changes:
- `create()` is now a plain single-row insert — the entity carries `file_path`
  + `size_bytes` (the runner computes both).
- new `update_pointer()` overwrites `file_path` / `size_bytes` / `title` /
  `description` in place — this is the `target_artifact_id` re-registration path.
- `iterate()`, `list_versions()`, `_row_to_version()` removed.
- `delete()` / `bulk_delete()` only remove the artifact row; on-disk source
  cleanup is the route layer's job (gated on `delete_source`).
- `total_bytes_for_user()` / `total_bytes_for_agent()` are plain `SUM(size_bytes)`
  off `instance_artifacts` — no join.

# Intent

Pure DB I/O for `instance_artifacts`. One row = one artifact = one pointer to an
entry file in the agent's workspace. Business rules (quota limits, path
validation, kind checks) live upstream in `artifact_runner`; this layer is
deliberately dumb.

## Upstream

- `artifact_runner.register_artifact` — the production caller.
- `backend/routes/agents_artifacts.py` + `users_artifacts.py` — list / detail /
  pin / delete endpoints.
- Tests — `tests/repository/test_artifact_repository.py` (real in-memory SQLite).

## Downstream

- `AsyncDatabaseClient` (utils/database.py) — CRUD helpers + `execute` for raw SQL.
- `schema_registry` `instance_artifacts` table — row shape.
- `BaseRepository[Artifact]` — `get_by_id`, `get_by_ids`, `find`, `find_one`.

## Design decisions

- **No version table, no transactions.** A single artifact row is one write.
  `create()` / `update_pointer()` / `delete()` are each a single statement, so
  the old two-table atomicity concern is gone.

- `set_pinned` uses raw SQL for both pin and unpin because
  `AsyncDatabaseClient.update()` filters out `None` values, making it impossible
  to explicitly SET a column to NULL via the CRUD helper. On pin: saves current
  `session_id` into `original_session_id` (via `COALESCE` so a re-pin is a no-op
  on that column) and sets `session_id = NULL`. On unpin: restores `session_id`
  from `original_session_id` and clears `original_session_id`.

- `list_by_session()` uses raw SQL because the simple `filters` dict passed to
  `BaseRepository.find()` cannot express `AND pinned = 0` alongside `session_id`.

- `total_bytes_for_*` use a single SQL `SUM` (with `COALESCE(..., 0)` because
  `SUM` over an empty set returns NULL) instead of loading rows and summing in
  Python.

- Placeholder style is `%s` (MySQL convention). `AsyncDatabaseClient` translates
  to `?` for SQLite via `_mysql_to_sqlite_sql`.

## Gotchas

- `_entity_to_row()` coerces `pinned` to `1`/`0` because SQLite stores booleans
  as INTEGER.

- `_row_to_entity()` calls `_parse_bool()` on `pinned` because SQLite returns
  INTEGER (0/1), not Python `bool`.

- `_row_to_entity()` defaults `file_path` to `""` and `size_bytes` to `0` for
  legacy (pre-pointer-model) rows that never had these columns populated — such
  rows won't render but won't crash the list query either. They are hand-migrated
  per the cleanup TODO.

- `COALESCE(SUM(...), 0)` is required — bare `SUM()` over an empty set returns
  NULL and the `int()` cast would raise `TypeError` for a user with no artifacts.
