---
code_file: src/narranexus/platform/utils/db/database.py
last_verified: 2026-08-18
stub: false
---

## 2026-08-18 — 六个 CRUD 收敛为纯委派（**含公开契约变化**）

`execute` / `get` / `get_one` / `get_by_ids` / `insert` / `update` / `delete` /
`upsert` 此前都是「顶部 fast path 委派 + 尾部自己拼 SQL」两条路。尾部那份在
`insert`/`update`/`delete`/`upsert` 里引用了未定义的 `fetch`——**lazy client 首次
调用直接 `NameError`**，而 `pyproject.toml` 全局 ignore 了 F821 所以 lint 不报，
线上又都经 `db_factory` 预置 backend 走 fast path，于是它一直活着。

全部改为 `backend = await self._ensure_backend()` 后委派。`database.py` 1170 → 895 行。

**这不是纯重构，facade 的公开契约有三处变化**（用旧实现与新实现对同一个 sqlite
文件跑 37 个用例差分得出，三处都是修复方向）：

1. `filters={"col": None}` 现在生成 `IS NULL` 并真的匹配到行；此前 lazy 路径生成
   `= NULL`，在 SQL 里恒不为真，`get` 返回 `[]`、`get_one` 返回 `None`。**如果有
   代码依赖「查 None 返回空」，那是依赖一个 bug。**
2. 写入值统一经 backend 的 `_serialize_value`：dict/list → JSON 字符串、bool → 0/1、
   datetime → ISO 8601。此前 lazy 路径直接把裸值交给驱动。
3. `insert`/`update`/`delete`/`upsert` 从 `NameError` 变成可用。

**没有丢的**：`order_by` 的方向解析（含大小写与非法方向的回退）、`limit`/`offset`、
`fields` 投影、`get_by_ids` 的保序 + 缺失位 `None` 占位 + 去重、以及六条 ValueError
的**文案**——差分逐字相同。

**facade 唯一保留的业务语义是 `insert` 的 None 过滤**（backend 不做）：显式传 NULL
会覆盖列的 DEFAULT，在 NOT NULL 列上直接报错。它和两条 ValueError 一起放在
`_ensure_backend()` **之前**——这些是调用方的编程错误，先建一次连接既浪费，又会在
数据库不可达时让连接错误盖住真正的原因。

`upsert` 的 SQL 拼装交回 backend 才是对的：MySQL 发 `AS new_row ON DUPLICATE KEY
UPDATE`（8.0.20+），SQLite 发 `ON CONFLICT DO UPDATE`。客户端拼一份 MySQL 语句再
交给任意 backend 是错的分层。

## 2026-08-18 — 删掉不可达的 legacy pool 路径（铁律 #2）

`AsyncDatabaseClient` 此前有两套实现：委派给 `DatabaseBackend` 的一套，和直接操作
`aiomysql` 连接池的另一套。后者**走不到** —— `_ensure_backend()` 的三条分支（sqlite /
sqlite-proxy / mysql）都会设 `self._backend` 然后 `return None`，从不给 `self._pool`
赋值；`self._pool` 只能由 `__init__(_pool=...)` 填入，而全仓没有任何调用方传它。

它不只是死的，它已经开始漂：2026-08-17 的事务修复不得不写两遍，而这份拷贝**静默漏掉了
主实现刻意加的四道守卫**（`getattr` 取 `_wakeup`、done callback 取 `exception()`、
写入口的继承事务拒绝、坏连接归还）。「必须同时改两处、其中一处没有任何测试」不是一个能
维持的结构。

删除范围：`__init__` 的 `_pool` 参数、各 CRUD 方法里 `pool = self._pool` 之后的整段、
`begin_transaction`/`commit`/`rollback`/`close` 的第二份实现，以及随之无用的
`_own_txn` / `_reject_inherited_write` / `_owned_or_raise` / `_return_to_pool` /
`_wakeup_tasks` / `_txn_conn` / `_POOL_CLOSE_TIMEOUT_SEC`。`_ensure_pool` 更名为
`_ensure_backend` 并返回 backend——旧名字的返回类型标着 `aiomysql.Pool` 而实际恒为
`None`，是另一处误导。共减 98 行。

事务语义现在只有一份，在 [[db_backend_mysql.py]]。本文件头部早就写着单后端委派是设计
意图，现在代码也这么说了。

## 2026-08-18 — `probe()`：给 `/health` 一个有取消契约的探活

新增 `AsyncDatabaseClient.probe()`，委派给后端。契约见
[[db_backend.py]]：**必须走普通连接池路径**（有私有通道的探测器证明不了线上的事），
且**取消时不得把连接还回池**。

## 2026-08-17 — legacy 路径的事务连接同样改为 task 级

`AsyncDatabaseClient` 当时有两条路：`_backend` 非空时全部委派给 `DatabaseBackend`；
`_backend` 为空时走本文件自带的 pool + 游标实现（legacy）。**两条路各有一份完全相同
的事务连接 bug**，所以一并修，否则只修一半等于没修。改动与
[[db_backend_mysql.py]] 对称：`_transaction_connection` 实例属性 → `_txn_conn`
ContextVar（存 `(owner_task, conn)`，原因见对侧文档：ContextVar 会被子 task 继承，
所以必须比较 task 身份）；`commit`/`rollback` 加 `finally` 无条件清空；出错的连接先
`close()` 再 `release()`，并补一次 `pool._wakeup()`——aiomysql 0.3.2 只对未关闭的连接
调度唤醒，归还一条已关闭连接会腾出槽位却不通知 `acquire()` 的等待者；`close()` 给
`wait_closed()` 加超时后 `terminate()`，否则关停时会等一条永远不回来的连接直到被
SIGKILL。

**另外修了 `transaction()` 的取消漏洞。** 原实现是 `except Exception`，而
`asyncio.CancelledError` 在 Python 3.8+ **不继承 `Exception`**。客户端断连时
Starlette 会取消请求任务，于是事务中途被取消 → rollback 被整个跳过 → 连接永远不
归还连接池，服务端的事务也一直开着直到锁超时。现在捕获 `BaseException`，并且
rollback 自身失败时只记日志不抛，避免掩盖原始异常（此时 `rollback()` 内部的
`finally` 已经把连接归还了）。

顺带把 `commit()` 移出 `try` 挪到 `else`：commit 失败后再调 `rollback()` 只会撞上
"No active transaction" 从而掩盖真正的错误。

## 2026-08-10 — facade `get()` 补 `fields` 透传

三个后端(sqlite/mysql/proxy)与抽象基类的 `get` 都早已支持列投影,
唯独 facade 签名漏了它——所有调用方被迫 `SELECT *`,在含 MEDIUMTEXT
的表(events 的 event_log 可达数十 MB/行)上是隐性的整表物化风险。
补参数并在当时的 legacy 内联路径同样实现(backtick + validate_identifier)——该内联路径已于 2026-08-18 删除，见顶部条目。
首个受益方:manyfold 诊断端点的 events 摘要查询。

## Why it exists

`database.py` is the project's central database client. Every piece of code that needs to read or write data — repositories, modules, background services, MCP tools — communicates through `AsyncDatabaseClient`. A second critical responsibility lives here: `_mysql_to_sqlite_sql()` rewrites MySQL-flavored queries (backticks, `%s`, `ON DUPLICATE KEY UPDATE`, `NOW()`, etc.) before they reach a SQLite backend, letting all callers write MySQL syntax regardless of deployment environment. Keeping the translator here rather than inside the SQLite backend is intentional: `sqlite_proxy_server.py` also imports it directly to apply the same translation to HTTP-proxied raw SQL.

## Upstream / Downstream

**Receives from:** `settings.py` — `load_db_config()` and `settings.database_url` drive both the connection parameters and the backend-selection branch. `schema_registry.TABLES` is queried by `_get_unique_cols_for_table()` to build `ON CONFLICT(...)` targets for upserts.

**Consumed by:** `db_factory.py` (wraps `create_with_backend()` to produce the process-wide singleton); every class under `repository/` (all Repository subclasses call CRUD methods on the client); `sqlite_proxy_server.py` (re-exports `_mysql_to_sqlite_sql`); `utils/__init__.py` (re-exports `AsyncDatabaseClient` and `DatabaseClient` alias).

## Design decisions

**Backend-delegation pattern.** `AsyncDatabaseClient` originally embedded aiomysql pool logic directly. As SQLite and proxy backends were added, all concrete driver code was pushed into `DatabaseBackend` subclasses; the client now delegates every operation to `self._backend`. The legacy aiomysql pool attributes and the second inline SQL path were removed on 2026-08-18 — there is one path.

**Lazy initialization.** `AsyncDatabaseClient()` can be constructed without awaiting anything. The backend is created on the first awaited call in `_ensure_backend()`. This lets module constructors accept a `database_client` parameter without the caller needing to have previously awaited anything.

**`_owns_backend` flag.** When a client auto-switches to share the factory singleton's backend (the `url.startswith('sqlite')` branch in `_ensure_backend`), it sets `_owns_backend = False`. Calling `.close()` on such a client does nothing to the shared backend — only the factory's `close_db_client()` tears it down.


**`_mysql_to_sqlite_sql` is a module-level function, not a method.** This keeps it importable by `sqlite_proxy_server.py` without creating any instance.

## Gotchas

**Reserved-word columns without backticks.** The translator turns backticks into double-quotes, but columns whose names are MySQL reserved words (e.g., `trigger`, `key`) that appear unquoted in a raw SQL string are passed through unchanged. In SQLite they are treated as bare identifiers and produce `sqlite3.OperationalError: no such column` rather than a syntax error.

**`ON DUPLICATE KEY UPDATE` with unregistered tables.** `_get_unique_cols_for_table()` looks up the unique-index columns in `schema_registry.TABLES`. If the table is not registered there, it falls back to `[table_name]` as the conflict target — which is virtually always wrong. Upserts silently become plain inserts. Any table that needs upsert support must appear in the registry.

**Event-loop change after in-process restart.** `_ensure_backend` delegates to the factory singleton for SQLite URLs. Any `AsyncDatabaseClient` instance that has already cached `self._backend` holds a reference to the old event loop's backend. After a loop change those instances raise `aiosqlite` "Event loop is closed" errors. Always obtain the client via `await get_db_client()` rather than storing it as a long-lived instance attribute.

**New-contributor trap.** Calling `AsyncDatabaseClient()` — no `await` — looks like it returns a ready client, and in many cases it works fine due to lazy init. But if the first call made on it fails (e.g., missing `DATABASE_URL`), the error surfaces as a cryptic connection failure at the first awaited operation, not at construction time.

## 2026-08-07 — `get_by_ids` 支持 `fields` 投影

契约与 `get` 一致。动机是存在性检查：对着带宽列（MEDIUMTEXT）的表问「这些 id 在不
在」时，`SELECT *` 会把 payload 拖过网络再丢掉。第一个用例是
[[narrative_routing_audit_repository.py]] 的快照去重——它在 `select()` 的同步路径
上，每条用户消息都要付。`id_field` 始终被强制并入投影，否则保序用的 result_map 建
不起来。两个后端同步实现。
