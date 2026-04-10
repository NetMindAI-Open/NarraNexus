---
code_file: src/xyz_agent_context/repository/base.py
last_verified: 2026-04-10
stub: false
---

# base.py

## Why it exists

`BaseRepository` solves two problems that otherwise recur in every data-access class: the N+1 query problem and boilerplate CRUD plumbing. Without it, every time a service needed to load 100 instances it would issue 100 individual `SELECT` queries. The base class's `get_by_ids()` issues one `IN` query and maps results back in input order.

It is a Generic class (`BaseRepository[T]`) so type checkers know that `EventRepository.get_by_id()` returns `Optional[Event]`, not `Optional[Any]`.

## Upstream / Downstream

All 14 concrete repository classes in this directory extend `BaseRepository`. They inherit `get_by_id`, `get_by_ids`, `save`, `insert`, `update`, `delete`, `upsert`, `find`, and `find_one`. Each subclass must implement `_row_to_entity()` and `_entity_to_row()`. The underlying `AsyncDatabaseClient` (from `utils/`) is the actual MySQL driver wrapper that `BaseRepository` delegates to.

## Design decisions

**`save()` is "smart upsert via query-then-write"** — it first issues a `get_one` to check existence, then either inserts or updates. This is intentionally **not** concurrency-safe. The `upsert()` method is the concurrency-safe alternative that uses `INSERT ... ON DUPLICATE KEY UPDATE`. The documentation on `save()` explicitly calls out this race condition. Callers that need guaranteed atomic semantics must use `upsert()`.

**`get_by_ids()` deduplicates while preserving order**: calling `get_by_ids(["evt_1", "evt_1", "evt_2"])` issues one query for `["evt_1", "evt_2"]` and returns `[evt_1, evt_1, evt_2]` with the duplicate correctly re-expanded. This matters for callers that request the same entity multiple times (e.g., a Narrative that references the same Module Instance twice).

**`table_name` and `id_field` as class attributes**: subclasses set these once at class definition time rather than passing them to `__init__`. This prevents accidental misconfiguration if a repository is constructed in multiple places.

## Gotchas

**`BaseRepository.__init__` raises `ValueError`** if `table_name` is empty. This catches the case where a developer forgets to set it on the subclass. The error fires at repository instantiation time, not at import time — so it will only surface when the first database operation is attempted.

**`find()` returns an empty list, not `None`**, when no rows match. `find_one()` returns `None` when no row matches. Be careful not to `if result:` check a `find()` result intending to catch "no rows" — an empty list is falsy but so is a list with zero-value entities.

**Order of results from `get_by_ids()` matches the input order**, not the database return order. If the database returns rows in a different order, the base class re-maps them by ID. This means if you pass an ordered list expecting sorted results, you get them back in your requested order, not database-natural order.

## New-joiner traps

- `EmbeddingStoreRepository` does **not** extend `BaseRepository`. It operates directly on dicts because its data structure is too simple to justify the entity mapping overhead. This is the one exception to the "all repositories extend BaseRepository" rule.
- The `id_field` class attribute refers to the **business primary key**, not the database auto-increment `id` column. For example, `EventRepository.id_field = "event_id"` even though the events table also has an auto-increment `id`. Methods like `get_by_id()` query against `event_id`, not the numeric auto-increment column.
