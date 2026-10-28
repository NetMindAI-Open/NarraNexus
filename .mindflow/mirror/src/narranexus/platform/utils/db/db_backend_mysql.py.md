---
code_file: src/narranexus/platform/utils/db/db_backend_mysql.py
last_verified: 2026-08-18
stub: false
---

# db_backend_mysql.py

`MySQLBackend` — the `DatabaseBackend` implementation for cloud/server deployments, using an `aiomysql` connection pool.

## Why it exists

When the database layer was refactored to support pluggable backends, the MySQL-specific driver code (pool management, `%s` placeholders, backtick quoting, `ON DUPLICATE KEY UPDATE`) was extracted from `AsyncDatabaseClient` into `MySQLBackend`. This allows `AsyncDatabaseClient` to stay dialect-agnostic and lets `db_factory.py` select the backend based solely on the URL scheme. `MySQLBackend` is the backend for all production cloud deployments where `DATABASE_URL` does not start with `sqlite://`.

## Upstream / Downstream

**Instantiated by:** `db_factory.py` for MySQL URLs; also `database.py`'s `_ensure_backend` lazy-init path when auto-detecting MySQL.

**Implements:** `DatabaseBackend` (from `db_backend.py`).

**Depends on:** `aiomysql` for the connection pool and cursor operations.

## Design decisions

**`aiomysql.create_pool` for concurrency.** Unlike SQLite's single connection, MySQL supports many simultaneous connections. The pool size and recycle interval are configurable at construction time and default to 10 connections, 1-hour recycle. The pool is created at `initialize()`, not at construction, so the class can be instantiated synchronously.

**Transparent retry on InnoDB deadlocks (errno 1213).** `execute` and `execute_write` wrap the no-transaction path in `_retry_on_deadlock` — up to 3 attempts with 50/100/200 ms backoff + small jitter. The fix exists because cascading DELETE in `delete_agent` (77 rows × 13 tables) raced agent-run event INSERTs on EC2 2026-05-19 and surfaced as 4 `pymysql.err.OperationalError(1213, ...)` to ASGI clients. Retry is the canonical fix per MySQL docs. **Inside** an explicit transaction the wrapper is skipped on purpose: the caller owns the transaction boundary and re-running one statement would leave earlier ones un-rolled-back.

**`%s` placeholders, backtick-quoted identifiers.** MySQL uses `%s` for parameters and backticks for identifiers. All identifier strings passed to `get`, `insert`, etc. are validated by `_validate_identifier` (alphanumeric + underscore) and then backtick-quoted to avoid reserved-word collisions.

**`INSERT ... ON DUPLICATE KEY UPDATE ... AS new_row` for upserts.** The `upsert` method generates MySQL 8.0.20+ syntax using an alias (`new_row`) rather than the deprecated `VALUES()` function. This is more explicit and future-proof, but means the code will fail on MySQL versions older than 8.0.20.

**Transaction support via a task-scoped dedicated connection.** Transactions take one connection from the pool and hold it in `self._txn_conn`, a `ContextVar` carrying a mutable `(owner_task, conn)` holder. Every statement method resolves it through `_own_txn()`; when that returns `None` the call takes its own connection from the pool.

Ownership is compared, not merely presence, and the three resulting behaviours are the contract:

- **reads** from a task that inherited the transaction go to the pool (they simply do not see uncommitted rows — `gather`-ing queries inside a transaction body is ordinary);
- **writes** from such a task raise. Quietly giving them a pooled connection is worse than failing: the write autocommits, the enclosing rollback cannot undo it, and the caller is never told it left the transaction it appeared to be inside;
- **commit / rollback** from such a task raise. Ending someone else's transaction would release a connection the parent is still writing to, and the parent's own commit would then double-release.

Why a mutable holder rather than a tuple, why `begin_transaction` must allocate a fresh one, and why the whole thing exists at all: see the 2026-08-17 and 2026-08-18 entries below.

**Value serialization mirrors `SQLiteBackend`.** `_serialize_value` converts `bool` to `0/1`, `datetime` to ISO 8601 strings, and `dict/list` to JSON strings. This ensures the two backends produce compatible stored representations so data written by MySQL can be read back under SQLite (and vice versa for the proxy path).

**IS NULL handling.** `get`, `update`, and `delete` filter clauses detect `None` values and generate `IS NULL` SQL instead of `= NULL`, which would always be false in MySQL.

## Gotchas

**MySQL 8.0.20+ upsert syntax.** The `INSERT ... AS new_row ON DUPLICATE KEY UPDATE new_row.col = ...` syntax requires MySQL 8.0.20 or later. Older MySQL versions reject this syntax with a parse error. If you need to support older MySQL, the `upsert` method needs modification to use the deprecated `VALUES(col)` form.

**Pool exhaustion under high concurrency.** The default pool size is 10. Long-running transactions or slow queries can hold connections, causing other coroutines to block waiting for a connection. Symptom: operations start timing out even though MySQL is healthy. Check `pool_size` against the expected concurrency.

**`_validate_identifier` rejects legitimate names with hyphens.** Column or table names containing hyphens (e.g., from external systems) will raise `ValueError` from `_validate_identifier`. This is intentional for SQL-injection prevention but can be surprising if you expect the validator to be lenient.

**New-contributor trap.** `aiomysql` cursors return tuples by default. `MySQLBackend` sets `cursorclass=aiomysql.DictCursor` to get dict rows. If you bypass the backend and use the raw pool directly, you will get tuples unless you explicitly pass the cursor class.

## 2026-08-18 — ContextVar 存**可变 holder**，以及 `probe()` 的取消契约

**为什么不能存不可变 tuple。** ContextVar 把**值**拷进每个之后创建的 task。存
`(owner, conn)` 元组时，父 task commit 后的 `set(None)` 只改父 task 自己那份 context，
于是事务体内 `create_task` 出来、活得比事务长的子 task 手里那份快照**永不过期**：它此后
每次写入都被守卫拒绝，而报错指向的那个事务早就结束了。

改成共享一个可变 `_Txn` holder：清空 `conn` 对父子同时可见，`conn is None` 对所有人都
意味着「没有事务」。`_clear_txn()` 负责清 holder 而不是清 context。

**`begin_transaction` 每次必须 new 一个 holder。** 复用会让父 task 的下一个事务把老子
task 的过期快照「复活」，比它替换掉的 bug 更难查。有专门的测试钉这一条。

**`probe()` 覆盖默认实现。** 默认走 `execute`，而 `async with pool.acquire()` 退出时
**无条件**归还连接；aiomysql 只丢弃已关闭的连接，`CancelledError` 不会关闭它。于是
`/health` 超时会把一条协议流可能已错位的连接放回 free list——正是本文件花力气用
`_return_to_pool(broken=True)` 避免的那一类。

要命的是触发时机：超时恰好在**数据库慢**的时候发生，也就是容器 healthcheck 每 30 秒
稳定复现一次，每次都可能再毒化一条池连接。为了让故障可见而加的探针，在故障期反过来
制造故障。覆盖后自己持有连接，异常路径（`except BaseException`，因为
`CancelledError` 不是 `Exception`）走 `broken=True`。

## 2026-08-17 — 事务连接从实例级改为 task 级（prod 事故根因）

**事故：** prod 09:37–09:56，登录与聊天历史全部 500，后端每次查询都是
`pymysql.err.InterfaceError: (0, 'Not connected')`，RDS 本身健康，只能靠重启恢复。

**根因链：**

1. 事务连接原先存在 `self._transaction_connection`（实例属性）。`db_factory`
   每个 event loop 只发一个 backend，于是这就是**进程级共享状态**。
2. 任一请求进入 `transaction()` 期间，**所有并发请求**的语句都命中
   `if self._transaction_connection is not None` 分支，被塞进同一条连接。触发者是
   `wipe_service` 的清空会话事务 —— 它在事务里串行跑几百次 delete，独占窗口极大。
3. 两个协程同时读同一 socket → aiomysql 抛
   `readexactly() called while another coroutine is already waiting for incoming data`
   → MySQL 协议流错位，连接死亡。
4. `commit()` 是**先 commit 再清空**且无 `try/finally`：commit 抛异常后
   `_transaction_connection` 永远指向那条死连接，此后进程内每条语句都改道到它。
   **永不自愈**，这是必须重启才能恢复的原因。

**修复：** ContextVar 隔离（爆炸半径回到单个 task）+ `commit`/`rollback` 用
`finally` 无条件清空 + 出错时先 `close()` 再 `release()`（aiomysql 的 `release()`
会丢弃已关闭连接，于是"毒化连接池"降级为"损失一条连接"）+ `begin()` 失败时立即
归还，避免连接泄漏。

**旧文档的教训：** 本文件此前那段写着"Concurrent calls ... are not safe; callers are
expected to use one backend instance per async task"——但 `db_factory` 的设计
恰恰保证了做不到。文档记下了危险，却把责任推给一个结构上不可能满足的前提，
等于没记。约束要写成代码里能成立的东西，写在文档里的承诺不算交付。

见 [[database.py]]（同一 bug 的第二份拷贝，一并修）与 [[db_factory.py]]（每 loop 单例）。

## 2026-08-07 — `get_by_ids` 支持 `fields`

沿用本文件 `get` 里已有的 `_validate_identifier` + backtick 写法；`id_field` 强制
并入投影，保证保序 map 可建。见 [[database.py]]。
