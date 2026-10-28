---
code_file: src/narranexus/platform/repository/user_settings_repository.py
last_verified: 2026-08-12
stub: false
---

## 2026-08-12 (r2 修正) — analytics setter 补齐 upsert

round-1 mirror 声称「两个 setter 收敛」但代码只改了 reply_language(文档>代码,已纠正):现在两个 setter 均为 db.upsert 原子写,get_one 仅存于读侧。

## 2026-08-12 — PR #284 review 轮

两个 setter 收敛为 `db.upsert`(review #4:手写 read-then-write 并发 PUT 撞唯一键 500,且被前端 fire-and-forget 吞掉)。

## 2026-08-11 — reply_language:回复语言偏好落库并注入 system prompt

新增 `get_reply_language`/`set_reply_language`(空串=清除,读侧归一为 None)。修「UI 选中文仍英文回复」:语言偏好此前只活在前端 i18n,从未落库。

# user_settings_repository.py

## Why it exists

Backs the `user_settings` table — per-user flat-column preferences introduced
with the product funnel instrumentation. The
first (and currently only) preference column is `analytics_opt_out`.

The class does NOT subclass `BaseRepository` because there is no Pydantic
entity schema for user settings (the table is a thin KV-style flag store, not
a rich domain object). It takes `AsyncDatabaseClient` directly and exposes two
focused methods.

## Upstream / downstream

- **Consumed by**: `analytics._opted_out()` — checks
  `is_analytics_opted_out(user_id)` before emitting any funnel event so that
  users who have opted out receive no tracking.
- **Depends on**: `AsyncDatabaseClient` from `narranexus.platform.utils`, and
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
