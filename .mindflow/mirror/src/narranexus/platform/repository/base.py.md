---
code_file: src/narranexus/platform/repository/base.py
last_verified: 2026-08-20
stub: false
---

# base.py

## 2026-08-18 — type-only import backing the `db_client` annotation

`AsyncDatabaseClient` is now imported under `if TYPE_CHECKING:` so the string
annotation on `__init__(self, db_client: 'AsyncDatabaseClient')` no longer
trips ruff's newly enabled F821 (undefined names in annotations). Type-only
because the class is referenced only in that annotation — there is no runtime
cycle to defend against (nothing under `utils/db/` imports `repository`); the
guard just keeps the runtime import graph unchanged. No behavior change.

## 2026-07-27 — module-level `parse_dt` export

Added `parse_dt(v)` at module scope: parse a timestamp column into an aware
datetime (naive → UTC), handling both the `datetime` MySQL returns and the ISO
string SQLite returns. Hoisted here because it was a byte-identical private copy
in `gateway_session_key_repository`, `quota_repository`, and
`artifact_repository`; all three then switched to `from .base import parse_dt`.
(2026-08-18: the first two files have since been deleted; today's importers
are `artifact_repository`, `team_bulletin_repository`, and
`team_workspace_repository`.)

## Why it exists

`BaseRepository` solves two problems that otherwise recur in every data-access class: the N+1 query problem and boilerplate CRUD plumbing. Without it, every time a service needed to load 100 instances it would issue 100 individual `SELECT` queries. The base class's `get_by_ids()` issues one `IN` query and maps results back in input order.

It is a Generic class (`BaseRepository[T]`) so type checkers know that `EventRepository.get_by_id()` returns `Optional[Event]`, not `Optional[Any]`.

## Upstream / Downstream

Most concrete repository classes in this directory extend `BaseRepository`; a sizable minority are deliberately standalone (see New-joiner traps — the class definition is the authority, not any count written here). Subclasses inherit `get_by_id`, `get_by_ids`, `save`, `insert`, `update`, `delete`, `upsert`, `find`, and `find_one`. Each subclass must implement `_row_to_entity()` and `_entity_to_row()`. The underlying `AsyncDatabaseClient` (from `utils/`) is the actual MySQL driver wrapper that `BaseRepository` delegates to.

## Design decisions

**`save()` is "smart upsert via query-then-write"** — it first issues a `get_one` to check existence, then either inserts or updates. This is intentionally **not** concurrency-safe. The `upsert()` method is the concurrency-safe alternative that uses `INSERT ... ON DUPLICATE KEY UPDATE`. The race is called out in `upsert()`'s docstring (its "Difference from save()" section), not on `save()` itself. Callers that need guaranteed atomic semantics must use `upsert()`.

**`get_by_ids()` deduplicates while preserving order**: calling `get_by_ids(["evt_1", "evt_1", "evt_2"])` issues one query for `["evt_1", "evt_2"]` and returns `[evt_1, evt_1, evt_2]` with the duplicate correctly re-expanded. This matters for callers that request the same entity multiple times (e.g., a Narrative that references the same Module Instance twice).

**`table_name` and `id_field` as class attributes**: subclasses set these once at class definition time rather than passing them to `__init__`. This prevents accidental misconfiguration if a repository is constructed in multiple places.

## Gotchas

**`BaseRepository.__init__` raises `ValueError`** if `table_name` is empty. This catches the case where a developer forgets to set it on the subclass. The error fires at repository instantiation time, not at import time — a subclass missing `table_name` only blows up when something first constructs it.

**`find()` returns an empty list, not `None`**, when no rows match. `find_one()` returns `None` when no row matches. Don't check a `find()` result with `is None` — it never is; "no rows" from `find()` is `[]`, and the two methods signal absence differently.

**Order of results from `get_by_ids()` matches the input order**, not the database return order. If the database returns rows in a different order, the base class re-maps them by ID. This means if you pass an ordered list expecting sorted results, you get them back in your requested order, not database-natural order.

## New-joiner traps

- Not every repository extends `BaseRepository`, and the standalone ones say so on purpose in their own docstrings: the append-only audit/analytics stores, `UserSettingsRepository`, the seen-message dedup stores (`channel_seen_message` / `lark_seen_message` — "deliberately not a `BaseRepository` subclass"), `SocialNetworkRepository` (backed by the unified memory engine, not the db client), and the raw-SQL `TeamFileRepository` / `ArtifactHistoryRepository` in `team_workspace_repository.py`. Read the class definition before assuming inheritance; don't "fix" a standalone one into a subclass. (The old "EmbeddingStoreRepository is the one exception" note described a class that no longer exists.)
- The `id_field` class attribute refers to the **business primary key**, not the database auto-increment `id` column. For example, `EventRepository.id_field = "event_id"` even though the events table also has an auto-increment `id`. Methods like `get_by_id()` query against `event_id`, not the numeric auto-increment column.

## 2026-08-18 — 公开只读 `db` property

domain-impl 代码(artifact 事件staging)需要在自家表之外发写。伸手拿 `_db` 是越界,
所以给一个只读暴露;不支持中途换 client。

## 2026-08-20 — `db` property 已删(#334 I9)

原始 client 逃逸口撤销:events 表归 [[artifact_event_repository.py]],
需要 db 的 impl 由 service 显式传参(register/heal/open_url 的 `db`
形参)。「repository 是唯一入口」恢复为规则而非建议。
