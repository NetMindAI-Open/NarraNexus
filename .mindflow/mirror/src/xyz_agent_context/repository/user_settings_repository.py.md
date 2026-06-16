---
code_file: src/xyz_agent_context/repository/user_settings_repository.py
last_verified: 2026-06-08
stub: false
---

# user_settings_repository.py

## Why it exists

Backs the `user_settings` table — per-user flat-column preferences introduced
with the analytics funnel instrumentation (Task 4 of the PostHog spec). The
first (and currently only) preference column is `analytics_opt_out`.

The class does NOT subclass `BaseRepository` because there is no Pydantic
entity schema for user settings (the table is a thin KV-style flag store, not
a rich domain object). It takes `AsyncDatabaseClient` directly and exposes two
focused methods.

## Upstream / downstream

- **Consumed by**: `analytics/_opted_out_sink.py` (or equivalent) — checks
  `is_analytics_opted_out(user_id)` before emitting any funnel event so that
  users who have opted out receive no tracking.
- **Depends on**: `AsyncDatabaseClient` from `xyz_agent_context.utils`, and
  the `user_settings` table registered in `schema_registry.py`.

## Design decisions

**Missing row = not opted out.** The `is_analytics_opted_out` read path treats
a missing row as `False` (tracking on by default). This avoids needing to
back-fill a row at user creation time — the row is created lazily on the first
`set_analytics_opt_out` call.

**No `updated_at` in the update dict.** `db.update()` uses parameterized SQL
placeholders (`%s`) so passing the string `"(datetime('now'))"` as a value
would store the literal text rather than evaluate the SQL expression. The
column holds the insert-time value on updates. If live update tracking becomes
needed, add a SQLite trigger or a separate timestamp update path.

**Insert-or-update via explicit existence check.** The pattern is:
`get_one` → branch on presence → `update` or `insert`. This is intentional:
the table has a UNIQUE index on `user_id`, so a raw INSERT on an existing row
would raise a constraint error rather than silently update.
