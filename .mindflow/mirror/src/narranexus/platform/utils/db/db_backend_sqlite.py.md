---
code_file: src/narranexus/platform/utils/db/db_backend_sqlite.py
last_verified: 2026-08-18
stub: false
---

## 2026-08-17 — 已知缺陷：事务归属与 commit 失败（**尚未修复**，方案已验证）

记在这里是因为修复曾经写出来又被撤回，两条缺陷都**已复现**，而复现的成本不该随
commit message 一起沉底。源码目前仍是有缺陷的版本。

**缺陷 1 —— 无关写入被并进别人的事务。** `_in_transaction` 是实例属性，而
`db_factory` 每 loop 只发一个 backend，所以它是进程级共享状态。任一 task 在事务中
期间，其他 task 的写入命中 `if not self._in_transaction` 为假 → 跳过 commit → 被并进
一个它毫不知情的事务；那个事务一回滚，这笔写入跟着消失，而调用方已拿到成功返回。
复现：A 开事务写 'a'，B 写 'b' 返回成功，A 回滚 → 读到 `[]`，'b' 一起没了。

**缺陷 2 —— 一次 commit 失败后此后每次写都跳过 commit。** `commit()` 是
`await conn.commit()` 成功后才清标志且无 `finally`，失败即永久卡在 True，直到重启。

这是 [[db_backend_mysql.py]] 那条 prod 事故的孪生版。MySQL 有连接可以被打死所以炸成
500；SQLite 只有一条连接，什么都不会坏，代价直接变成**静默数据丢失**——而桌面 / DMG
与本地 `run.sh` 跑的就是 SQLite（铁律 #7）。

**修的时候不要用 `current_task()` / ContextVar 做归属——已验证会卡死 proxy。**
`sqlite_proxy_server` 的 `/transaction/begin`、写端点、`/transaction/commit` 是三个
独立 HTTP 请求，uvicorn 每请求一个 task，begin 那个 task 的 context 随请求结束即消失，
此后归属判断恒为 False。真 uvicorn 实测：写请求死锁在事务独占的写锁上，commit 报
"No active transaction"，watchdog 的 rollback 同样撞 RuntimeError 被 `except Exception`
吞掉，锁和 BEGIN 都不释放 → **该 proxy 进程内所有写入永久阻塞，只能重启**。

**正确方向**：proxy 早就把跨请求事务做对了——它有一套 server 发放的 `txn_id` token 和
`_await_txn_turn(txn_id)` 门禁。归属应当改成**可显式传递的 token**（缺省取
`current_task()` 以保持应用内调用不变，proxy 传它自己的 `txn_id`），两层对齐而不是在
下面再发明一套隐式机制。commit/rollback 加 `finally`，且 commit 失败时补一次
`rollback()`（与 MySQL 侧 `broken=True` 先 `close()` 让服务端回滚的语义对齐）。

**测试必须避开这次绕开过的两个坑**：(1) 用**独立 `sqlite3` 连接读已提交数据**来断言，
而不是断言锁状态——上一版验证了锁被释放，却对紧随其后那笔把脏数据提交掉的写入零断言；
(2) proxy 用例必须把 begin/write/commit 各自包进 `asyncio.create_task(...)` 并带
`asyncio.wait_for` 上限，现有 `test_sqlite_proxy_txn.py` 用 `httpx.ASGITransport`，
ASGI app 在调用方同一个 task 内执行，ContextVar 天然可见，所以这个 Critical 在一整套
绿灯里完全隐形。

## 2026-08-17 — aiosqlite worker 线程不许死在"投递结果"这一步

模块导入时把 `aiosqlite.core._connection_worker_thread` 换成
`_resilient_connection_worker_thread`。上游的写法是：语句执行完用
`future.get_loop().call_soon_threadsafe(...)` 把结果交回去；如果发起该语句的
loop 已经关闭，这一句抛 RuntimeError，而它的 `except` 分支**又调了同一个方法**，
再抛一次，没人接——**worker 线程就此静默死亡，手里还攥着一条打开的 sqlite3
连接**。

之后没有任何东西能回收它：文件句柄和那笔被放弃的语句持有的锁一直在，后续每个
writer 都要走满 10 次重试 x 30 秒 busy_timeout（~5 分钟）才等到
"database is locked"。触发条件一点都不罕见，但**不是 MCP 容器**：`module_runner.py` 用
multiprocessing，每个 module 一个进程 + 一个进程寿命的 `asyncio.run`，零
`threading.Thread`。真正在造短命 loop 的是 `get_db_client_sync()` 里那个一次性
`asyncio.run`（`ContextRuntime` 在没有注入 client 时就走这条路）、
`lark_trigger` 每次重连的 fresh loop、一次性脚本 / migration、以及测试 harness。
另一半是摸数据库的 fire-and-forget 任务被自己 loop 的关闭撞上——
`schedule_user_no_quota_rearm` 是一个，不过它跑在 uvicorn 主 loop 上，所以只在
服务关闭时才咬人。

处理原则：**丢结果可以，死线程不行**。等待它的协程已经随 loop 一起没了，结果
本来就无人接收；但这条线程是唯一能关掉这条连接的人。这是项目事故清单第 2 条
（第三方的 fire-and-forget 同样是雷）落在 aiosqlite 上的具体一例，替换范围刻意
收窄：只换"投递"那一步，队列协议和 STOP 哨兵仍是上游的。

**这个补丁有两半，缺一半就是把慢变成挂死。** 上游的 worker 是**非 daemon**
线程，它一直（阴差阳错地）靠"投递失败就死"来回收孤儿——线程死了进程才能退出。
只加投递加固不加 daemon，`Py_FinalizeEx` 会在模块析构**之前**跑
`threading._shutdown()` join 掉这条非 daemon 线程，`Connection.__del__ → stop()`
永远轮不到，于是"5 分钟写阻塞"变成"进程永远退不出"。2026-08-17 实测（连接被
压在 `BEGIN IMMEDIATE` 后面成为孤儿）：上游 1.4 秒退出，只打投递补丁则永不退出。
那会落在桌面版上（SQLite 就是桌面后端，铁律 #7）变成"退出后留一个僵尸攥着 DB
文件"，在容器里变成每次 `docker stop` 都吃 SIGKILL——比它替换掉的那个慢**更难
诊断**。所以构造函数也被包了一层，把 `_thread.daemon` 设为 True（只能在
`start()` 之前设，而 aiosqlite 在 `__await__` 里 start，因此不能放
`initialize()`）。这**不给弱化关闭路径开口子**：daemon worker 在退出时可能被
中途杀掉（安全，WAL 本身崩溃安全，等同 SIGKILL），但**正常的关闭必须继续显式
进行**，`close_db_client()` 和测试套件的 `pytest_sessionfinish` 依旧承重。

投递里的 `RuntimeError` **不是无条件吞掉**：loop 已关闭是预期情形，保持安静；
loop 还活着却拒收回调是另一回事，那意味着 aiosqlite 里那句没有超时的
`await future` 永远不会完成——一次永久且无声的 DB 停顿，正是本补丁要消灭的那种
"看不见的症状"。后者打 WARNING（事故清单第 3 条：异常过滤必须精确到类**和**
上下文）。

**第三层是 import 期的存在性检查**（这一层此前 mirror 没写，2026-08-17 补）。
补丁读/写六个 `aiosqlite.core` 内部名：调用时解析的 `LOG` / `set_result` /
`_STOP_RUNNING_SENTINEL` / `set_exception`，赋值目标 `_connection_worker_thread`，
以及被包构造函数的 `Connection` 类本身。**给一个上游已经改名的属性赋值是静默成功
的**，所以模块导入时逐个 `hasattr` 检查，缺任何一个就抛 `ImportError` 而不是让
加固悄悄降级成 no-op。`Connection.__init__` **故意不在这个元组里**——
`hasattr(cls, "__init__")` 对任何类恒真，那是条恒真守卫（曾经有过，已删）；它真正
的兜底是 `_daemon_worker_connection_init` 里的 `hasattr(self, "_thread")`。
`pyproject.toml` 的 `aiosqlite>=0.22.1` 只排除**更旧**的版本；**更新**的版本改名
只能靠这一层抓。

配套修复见 [[db_factory.py]]（驱逐时真的关连接）。守卫见
`tests/utils/db/test_sqlite_orphaned_connections.py`，其中两条分别断言这两个
猴补丁**确实装上了**（投递函数被替换、worker 是 daemon）——它们都是 import 副
作用，重构时很容易丢，而丢了不会报错：一个让每次碰撞多花 5 分钟，另一个让进程
再也退不出。

顺带记下一个没在本次改动的放大器：`_retry_write` 的 10 次重试与
`PRAGMA busy_timeout=30000` 是**相乘**的（每次重试都在 SQLite 里等满 30 秒），
最坏情况一次写阻塞 ~5 分钟。作者多半以为"10 次快速重试"。

## 2026-08-07 — `get_by_ids` 支持 `fields`

与 MySQL 后端同形，标识符用双引号。见 [[database.py]]。

顺带记一个已经咬过人的行为（本次没改，只是写明）：`get_by_ids` 为了**保持输入
顺序**，会给查不到的 id **补 `None` 占位**（`result_map.get(id_val)`）。调用方直接
对返回行下标就会 `TypeError`，而如果调用方外面套了 advisory except，整批会被静默
吞掉。


## 2026-05-22 — initialize() guards the parent dir (clear error, not cryptic)

Before `aiosqlite.connect`, `initialize()` ensures the DB's parent dir
(`~/.narranexus`) is writable via `fs_safety.ensure_writable_dir` (which repairs
perms on a dir we own). Unlike logs, the DB CANNOT be diverted to a temp dir —
it's the user's data — so if the dir is foreign-owned (e.g. carried over by
Migration Assistant) it raises a clear `RuntimeError` naming the dir + the exact
`sudo chown` fix, instead of aiosqlite's cryptic `unable to open database file`
that (pre readiness-gate) killed the proxy/backend on startup. `:memory:` skipped.

# db_backend_sqlite.py

`SQLiteBackend` — the `DatabaseBackend` implementation for local/desktop deployments, using `aiosqlite` with WAL mode and a serializing write lock.

## Why it exists

The Tauri desktop migration needed a file-based database that runs without a server process. `db_backend_sqlite.py` provides the concrete SQLite driver that `AsyncDatabaseClient` delegates to when `DATABASE_URL` starts with `sqlite://`. It wraps `aiosqlite` (async SQLite via a thread pool) and adds three layers of application-level concerns that SQLite itself doesn't handle the same way MySQL does: write serialization, automatic timestamp parsing, and value serialization for composite Python types.

## Upstream / Downstream

**Instantiated by:** `db_factory.py` when no `SQLITE_PROXY_URL` is set and the URL scheme is `sqlite`.

**Implements:** `DatabaseBackend` (from `db_backend.py`), so `AsyncDatabaseClient` uses it transparently.

**Depends on:** `aiosqlite`, `db_backend.py` (ABC), stdlib `asyncio` and `json`.

## Design decisions

**Single long-lived connection, not a pool.** SQLite is a file — there is no network round-trip to amortize. A single `aiosqlite.Connection` is opened at `initialize()` and kept for the backend's lifetime. Connection overhead is negligible compared to the overhead of opening a new file handle per query.

**`asyncio.Lock` for write serialization.** SQLite allows only one writer at a time within a process. Rather than relying on SQLite's retry timeout, the backend holds a write lock before executing any `INSERT`, `UPDATE`, `DELETE`, or `UPSERT`. Reads (`SELECT`) bypass the lock to maximize concurrency under WAL mode.

**WAL journal mode.** `PRAGMA journal_mode=WAL` is set at `initialize()`. WAL allows multiple concurrent readers even while a write transaction is in progress, which is essential for the agent pipeline where many coroutines read context data while background services write module state.

**Automatic timestamp parsing in `_auto_parse_row`.** SQLite stores all datetime values as TEXT. Rather than forcing every caller to parse timestamps, the backend converts columns whose names match known suffixes (e.g., `_at`, `_time`, `created_at`) to Python `datetime` objects when rows are returned. The detection is suffix-based, not universal, to avoid false positives on non-timestamp TEXT columns.

**JSON/dict/list serialized to strings.** Python dicts and lists passed to `insert` or `update` are serialized to JSON strings before storage. On read, the backend does not auto-deserialize JSON (unlike timestamps) — callers that store JSON must `json.loads()` the returned string themselves. This asymmetry is intentional: timestamp conversion is safe and universal, but auto-deserializing every TEXT column that looks like JSON would be error-prone.

**`upsert` uses `INSERT OR REPLACE`.** SQLite's `INSERT OR REPLACE` deletes the conflicting row and re-inserts, which resets auto-increment IDs and triggers `ON DELETE` cascades if any exist. An alternative `ON CONFLICT DO UPDATE` approach was not chosen here; callers that care about preserving the row ID should check whether this matters for their table.

## Gotchas

**Timestamp parsing by suffix, not by type.** If a new TEXT column is added whose name ends in `_at` but does not contain a datetime value, `_auto_parse_row` will attempt to parse it and either return a garbled `datetime` or fall back to the raw string. Avoid naming non-timestamp columns with timestamp suffixes.

**Write lock is per-backend-instance, not per-file.** If two `SQLiteBackend` instances are created pointing at the same file path (which should not happen in production because `db_factory.py` enforces a singleton, but can happen in tests), their write locks are independent and they will race. The symptom is `sqlite3.OperationalError: database is locked`. Use the proxy backend in any multi-process setup.

**WAL files accumulate after a crash.** If the process is killed mid-write, the `-wal` and `-shm` sidecar files remain on disk. SQLite handles recovery automatically on the next open, but the presence of these files can confuse backup scripts that copy only the main `.db` file.

**New-contributor trap.** `aiosqlite` runs SQLite in a thread pool under the hood. Calling a synchronous SQLite operation on the `aiosqlite` connection object directly (without `await`) will block the event loop thread. Always use `async with conn.execute(...)` patterns.
