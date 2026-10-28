---
code_file: src/narranexus/platform/utils/db/db_factory.py
last_verified: 2026-08-17
stub: false
---

## 2026-08-17 — 驱逐必须真的关掉连接，否则锁被永久孤儿化

`_evict_closed_loops()` 以前只把 client 从三张表里 `pop` 掉。**忘掉一个 client
不等于释放它**：被驱逐的 client 仍持有活的 backend 连接——SQLite 情况下是一条
aiosqlite worker 线程 + 一个打开的文件句柄，如果那个 loop 是在写到一半时消失
的，还有一笔**永远不会 commit 也不会 rollback 的写事务**。注册表是最后一个引用，
所以它丢掉而不关掉的东西，按定义就是孤儿。

代价不是"慢"，是一把没人会松开的锁：之后每个 writer 都要在
`_MAX_WRITE_RETRIES`(10) x `busy_timeout`(30s) 上耗掉 ~321 秒才抛
"database is locked"。2026-08-17 profile 测试套件时，六次这样的碰撞占了 38 分钟
里的 92%，而且**六个测试全是绿的**——症状是"慢"，没有任何红色指向它。修完
38 分钟 → 3 分钟。

因此本函数改为 `async`，驱逐时在**当前** loop 上 `await client.close()`
（aiosqlite 关闭只需要"某个"在跑的 loop，不必是当初那个——与 `close_db_client`
同一条推理）。

扫描用**一个总预算**（`_EVICT_SWEEP_BUDGET`，5 秒）而不是每条一个超时：
`stale_ids` 的长度由调用方的 loop churn 决定，没有上限，而这个扫描就压在
`get_db_client()` 命中缓存的返回路径**前面**——按条计时等于让制造 loop 的人
决定下一个取 client 的人付多少延迟（调用方可控基数无上限，正是本仓反复中招的
那个模式）。预算耗尽时条目照样丢弃（id() 冲撞是绝不能留的那一半），连接不回收，
并且**明说**而不是装作成功。三个 `pop` 都在 `await` 之前，这是同一个 loop 上两
个协程不会重复 close 同一个 client 的原因，**不要**"顺手整理"到 await 之后。

两条必须写明的边界，否则上面那句标题是在超卖：

- **回收实际只对 SQLite 有效。** `MySQLBackend.close()` 走
  `pool.wait_closed()`，它会在已死的 loop 上 `call_soon(...)` 并等一个绑在该
  loop 上的 `Condition`。MySQL 上这会稳定失败而不是回收，你只会拿到一条
  warning。**尚未对着真 MySQL 验证过，所以这里不声称修好了 MySQL**——对 MySQL
  而言本函数仍然只解决 id() 冲撞。
- **`SYNC_KEY` 够不着。** `get_db_client_sync()` 只登记
  `_clients_by_loop[SYNC_KEY]`，从不写 `_loops_by_id`，而 `stale_ids` 正是从
  后者推导的——于是**最必然拥有一个死 loop 的那个 client 被结构性排除在外**，
  只有 `close_db_client()` 会回收它。而 `ContextRuntime.__init__` 在没有注入
  client 时就走这条路，也就是说真有代码路径在铸造这种孤儿。要让它可驱逐，得先
  审计 `get_db_client_sync` 的所有调用方——否则进程里第一次
  `await get_db_client()` 就会把 bootstrap client 从还握着它的人手里关掉。

这条修复只有配合 [[db_backend_sqlite.py]] 的 aiosqlite worker 加固才完整——
worker 死了的话这里的 close 也没人执行，而那份加固本身也有两半（投递 + daemon
线程）。守卫见 `tests/utils/db/test_sqlite_orphaned_connections.py`。

## 2026-07-22 — MySQL pool size env-tunable (MYSQL_POOL_SIZE)

The MySQL construction path now reads `MYSQL_POOL_SIZE` (default 10, min 1,
invalid → 10) and passes it to `MySQLBackend(pool_size=...)`. Rationale: the
worker supervisor ([[run_worker_supervisor.py]]) drives poller + jobs + bus +
every channel trigger from ONE process = ONE pool, where the pre-consolidation
layout had 4 processes × their own pools. A supervisor deployment should raise
this to ≥25 so combined worker/subscriber concurrency doesn't starve on
`pool.acquire()`. SQLite path unaffected. (PR #136 review.)

## 2026-05-22 — parse_sqlite_url collapses leading `//`

Callers build the URL as `sqlite:///` + an already-absolute path → the
SQLAlchemy 4-slash absolute form `sqlite:////Users/...`. `parse_sqlite_url`
stripped only `sqlite://` (2 slashes), leaving a malformed `//Users/...` (macOS
tolerates it but it showed up as `database: //Users/...` in proxy logs). It now
collapses a leading run of slashes to one; `:memory:` / relative paths untouched.

# db_factory.py

Per-event-loop registry for `AsyncDatabaseClient` — resolves the backend
from the URL and hands each asyncio event loop its own pool.

## Why it exists

The original codebase had 40+ direct `DatabaseClient()` constructions, each
creating its own connection. MCP tool handlers were the worst case: the
agent runtime cannot inject a db client into MCP tools at call time, so
every tool call would open a new connection. `db_factory.py` centralises
that: one `get_db_client()` coroutine is the only way to acquire a client,
and it owns the backend-selection logic (`detect_backend_type`,
`parse_sqlite_url`).

**Why per-loop, not per-process** (2026-04-22 rewrite): the MCP container
runs every module in its own `threading.Thread` + `asyncio.new_event_loop`
via `module_runner.run_mcp_servers_async`. That means 8 concurrent asyncio
loops share one Python process. aiomysql's internal Futures (e.g.
`Pool._wakeup`) bind to the loop that created the pool; reusing the pool
from another loop raises "got Future attached to a different loop" — the
exact error that blew up `mcp__job_module__job_create` in production on
2026-04-22. The previous "singleton + recreate on loop change" design only
displaced the problem (whichever loop lost the race held stale Futures).

## Upstream / Downstream

**Reads from:** `settings.py` (via `settings.database_url`) and the
`SQLITE_PROXY_URL` environment variable.

**Instantiates:** `SQLiteBackend` (from `db_backend_sqlite.py`),
`MySQLBackend` (from `db_backend_mysql.py`), or `SQLiteProxyBackend`
(from `db_backend_sqlite_proxy.py`), wrapped by
`AsyncDatabaseClient.create_with_backend()`.

**Consumed by:** `database.py` (lazy-init auto-switch to SQLite);
`utils/__init__.py` (re-exports `get_db_client`, `get_db_client_sync`,
`close_db_client`); every MCP tool handler and background service.

## Design decisions

**Per-loop dict keyed by `id(loop)`.** `_clients_by_loop` maps
`id(running_loop) → AsyncDatabaseClient`. `_loops_by_id` keeps a strong
reference so we can detect closed loops; `_locks_by_loop` keeps a
per-loop `asyncio.Lock` so first-call races within a single loop are
serialised without cross-loop lock binding (a `asyncio.Lock()`
constructed while a given loop is running binds to that loop).

**Cheap eviction on every access.** `_evict_closed_loops()` iterates
`_loops_by_id` and drops any entry whose `loop.is_closed()` returned
true. This is O(n) in the number of loops the process has ever held
(typically < 10), runs before the hot-path lookup, and guards against
`id()` collisions when a new loop object is allocated at the same
memory address as a dead one.

**`close_db_client()` dispatches closes back to the origin loop.**
Closing a pool from a different loop than the one it was built on would
reintroduce the exact cross-loop bug we're fixing. When
`close_db_client()` runs from outside the origin loop, it uses
`asyncio.run_coroutine_threadsafe(client.close(), loop).result(timeout=5)`.
If the origin loop is already closed, the client is closed on the
CURRENT loop instead (2026-07-13). The earlier "drop the entry, process
exit reclaims it" assumption was wrong for the SQLite backend. It was
originally wrong because aiosqlite ran a NON-daemon worker thread per
connection, so an unclosed client blocked interpreter shutdown forever —
the "pytest prints its summary then hangs" bug. **That is no longer the
reason (2026-08-17):** [[db_backend_sqlite.py]] now makes that worker a
daemon thread, so it cannot hold the process open. The assumption is
still wrong, for the reason that outlived the hang — a daemon thread is
KILLED wherever it stands at interpreter exit, so this close is the only
point at which the connection's writes are drained and its SQLite locks
released on purpose rather than by process death. Do not drop the close
because the hang stopped happening. Cross-loop close is safe precisely because the
origin loop is gone: aiosqlite's close() only needs *a* running loop.

**`SYNC_KEY = -1` pseudo-loop-id for the sync bootstrap path.**
`get_db_client_sync()` is kept as an escape hatch for code paths that run
before any asyncio loop exists (top-level synchronous scripts). The
client it returns was built via a throwaway `asyncio.run()` loop and
**must not** be reused from async contexts — its pool is bound to a
loop that has already been torn down. Caching under `SYNC_KEY` merely
prevents `asyncio.run()` from being invoked twice for the same process.

**URL-scheme-based backend selection.** Decision tree: `sqlite://` +
`SQLITE_PROXY_URL` set → `SQLiteProxyBackend`; `sqlite://` alone →
`SQLiteBackend`; everything else → `MySQLBackend` with `load_db_config()`.
All environment-detection logic lives in this one file.

**`detect_backend_type` / `parse_sqlite_url` are module-level utilities.**
Also imported by `database.py`'s lazy-init path and by
`sqlite_proxy_server.py`. Keeping them here rather than in `database.py`
avoids a circular import (the factory imports from `database.py`, not
the reverse).

## Gotchas

**Multiple processes still do not share a pool.** The FastAPI backend,
MCP server, ModulePoller, job trigger, bus trigger, and Lark trigger
each run as separate Docker services; each gets its own per-loop
registry in its own process memory. Under SQLite this means concurrent
writes from different processes contend for the file lock — that is the
problem `sqlite_proxy_server.py` exists to solve; set
`SQLITE_PROXY_URL` to serialise all DB writes through the proxy.

**RDS connection budget scales with loops, not processes.** Each loop
builds a fresh aiomysql pool. On the MCP container alone that's 4
active modules × `pool_size` (default 10) = 40 connections. Add the
other 5 Python services at 10 each = another 50. Total ~90 idle
connections steady-state, more under burst. Confirm
`max_connections` on the RDS cluster is comfortably above this before
scaling out further.

**`close_db_client()` from the wrong thread hangs briefly.** It uses
`fut.result(timeout=5)` to bound the wait; after 5 s the close is
abandoned and the client entry is cleared anyway. Don't rely on
close_db_client for ordering against other shutdown tasks.

**Changing `DATABASE_URL` mid-process has no effect.** The factory reads
`settings.database_url` only when a loop first requests a client.
Subsequent calls return the cached per-loop singleton.

**`get_db_client_sync()` returns a dead-loop client.** Calling it and
then trying to use the result from an async context will fail with
"Event loop is closed." That's by design — the sync path exists for
sync callers only. Newer code should never need it.

## Historical context

- 2026-04-21 (`0aec35d`): removed `XYZBaseModule._mcp_db_client`
  class-level cache that shadowed the factory's loop-change detection.
  Necessary prerequisite but not sufficient — the factory was still a
  process-wide singleton that thrashed under multi-loop MCP.
- 2026-04-22 (this commit): factory itself becomes per-loop. See
  TODO-2026-04-22 R1 / BUG_FIX_LOG Bug 34 for full debug trail.
