# dataloader.py

GraphQL-DataLoader-style automatic batcher — coalesces scattered `load(id)` calls within the same event loop tick into a single `SELECT ... WHERE id IN (...)` query.

## Why it exists

The N+1 query problem appeared repeatedly in Narrative and Event retrieval: code that loads a list of items and then fetches related data for each item in a loop would issue N+1 separate queries. For example, loading 100 events for a Narrative, then fetching the Narrative object for each, would issue 100 individual SELECT queries. `dataloader.py` solves this the same way Facebook's GraphQL DataLoader does: all `load()` calls within the same event loop iteration are queued, and when the loop yields control, the batch function is called once with all queued keys.

## Upstream / Downstream

**Consumed by:** `repository/` (any Repository that uses `DataLoader` to batch-fetch related entities), `narrative/` (event batch loading), and `agent_runtime/` when loading multiple module instances. Re-exported from `utils/__init__.py`.

**Depends on:** stdlib `asyncio`. The `batch_load_fn` that callers provide typically calls `db.get_by_ids()` from `database.py`.

## Design decisions

**`asyncio.Task` dispatch via `call_soon`.** When the first `load()` call arrives, `DataLoader` schedules `_dispatch_batch()` using `asyncio.get_event_loop().call_soon()`. This ensures the batch runs after all synchronous code in the current task has finished queuing its keys — the same mechanism Facebook's DataLoader uses.

**Optional per-instance cache.** With `cache=True` (default), keys that have already been loaded are not re-queued; their futures resolve immediately from the cache. This is useful for read-heavy workloads where the same entity is referenced multiple times per request. Disable with `cache=False` for mutable data that may change between calls.

**`max_batch_size` chunking.** If more keys are queued than `max_batch_size` (default 100), the batch is split into multiple calls to `batch_load_fn`. This prevents `IN (...)` clauses from becoming too large for the database engine.

**`batch_load_fn` must return results in input order.** The function receives `List[K]` and must return `List[Optional[V]]` of the same length and in the same order, with `None` in slots where the key was not found. This contract mirrors `DatabaseBackend.get_by_ids`. Violating it silently corrupts all results.

## Gotchas

**Batching only works across `await` boundaries in the same event loop cycle.** If you call `loader.load(id1)` and immediately `await` it before calling `loader.load(id2)`, the two calls are in different event loop cycles and each becomes its own query. To benefit from batching, call `load()` for all keys before awaiting any of them, or use `load_many()`.

**Cache is not invalidated on write.** `DataLoader` has no write-through or write-around invalidation. If code writes to a table and then reads from a cached `DataLoader`, it will get stale data. Either disable caching for mutable entities or create a fresh `DataLoader` per request.

**New-contributor trap.** `DataLoader` is a per-request or per-operation object, not a global singleton. Creating one `DataLoader` per `Repository` class attribute and reusing it across requests will serve stale cached data to subsequent requests (the cache is never cleared). Instantiate a new `DataLoader` per agent run or per HTTP request.
