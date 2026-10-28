"""
@file_name: db_backend_mysql.py
@author: NexusAgent
@date: 2026-04-02
@description: MySQL implementation of the DatabaseBackend interface

Uses aiomysql for async MySQL access. Designed for cloud/server deployment.

Key design decisions:
- Connection pool via aiomysql.create_pool (configurable size and recycle)
- %s parameter placeholders, backtick-quoted identifiers
- INSERT ... ON DUPLICATE KEY UPDATE with AS new_row syntax (MySQL 8.0.20+)
- Transaction support using a dedicated connection from the pool, bound to the
  calling asyncio task via a ContextVar (see `_txn_conn`) — NOT to the backend
  instance. A backend instance is shared by every request on the event loop, so
  an instance attribute made one caller's open transaction the implicit
  connection for every concurrent caller. See the class docstring.
- IS NULL handling for None filter values in get/update/delete
- JSON/dict/list values serialized to JSON strings for storage
- Boolean values stored as 0/1 integers
- datetime values stored as ISO 8601 strings
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from contextvars import ContextVar
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

import aiomysql
import pymysql.err
from loguru import logger

from narranexus.platform.utils.db.db_backend import DatabaseBackend


# InnoDB deadlock errno. MySQL aborts the "lighter" transaction; the
# client is expected to retry. See:
# https://dev.mysql.com/doc/refman/8.0/en/innodb-deadlocks-handling.html
_DEADLOCK_ERRNO = 1213

# How long shutdown waits for borrowed connections before terminating them.
#
# Must stay STRICTLY BELOW the caller's own budget. `db_factory._evict_closed_loops`
# wraps `client.close()` in `wait_for(..., remaining)` where `remaining` is what
# is left of a 5s sweep — always < 5s. With both at 5s the outer timeout always
# won, `close()` was cancelled inside its own `wait_for`, and the `terminate()`
# fallback below never ran: the pool leaked instead of being reclaimed, which is
# exactly the path this fallback exists for.
#
# Short is also right on its own terms: an in-flight transaction still running
# at shutdown is going to be rolled back by the server anyway, and a container
# that will not exit is a redeploy that hangs until docker SIGKILLs it.
_POOL_CLOSE_TIMEOUT_SEC = 2.0


async def _retry_on_deadlock(
    coro_factory: Callable[[], Awaitable[Any]],
    max_attempts: int = 3,
) -> Any:
    """Re-invoke `coro_factory()` if it raises a MySQL deadlock error.

    Only retries on `pymysql.err.OperationalError` with errno 1213
    ("Deadlock found when trying to get lock; try restarting
    transaction"). Other OperationalError subtypes (e.g. 2003 / connect
    refused) and unrelated exceptions propagate immediately.

    Backoff is short and randomised — typical InnoDB deadlocks resolve
    in microseconds and we just need to give the surviving transaction
    time to commit.

    Callers must only wrap statement-level work that does NOT already
    sit inside an explicit transaction. Inside a transaction the boundary
    is owned by the caller; re-running a single statement would leave
    the earlier statements un-rolled-back.
    """
    for attempt in range(max_attempts):
        try:
            return await coro_factory()
        except pymysql.err.OperationalError as e:
            if (
                e.args
                and e.args[0] == _DEADLOCK_ERRNO
                and attempt < max_attempts - 1
            ):
                # 50ms, 100ms, 200ms ... with up to 50ms jitter
                backoff = 0.05 * (2 ** attempt) + random.random() * 0.05
                logger.warning(
                    f"[MySQLBackend] deadlock (errno 1213) on attempt "
                    f"{attempt + 1}/{max_attempts}; retrying in "
                    f"{backoff:.3f}s"
                )
                await asyncio.sleep(backoff)
                continue
            raise
    # Unreachable — the loop either returns or raises.
    raise RuntimeError("unreachable")  # pragma: no cover


def _validate_identifier(identifier: str) -> str:
    """
    Validate table/column names to prevent SQL injection.

    Only allows alphanumeric characters and underscores.

    Args:
        identifier: The table or column name to validate.

    Raises:
        ValueError: If the identifier contains invalid characters.

    Returns:
        The validated identifier.
    """
    if not re.fullmatch(r"[A-Za-z0-9_]+", identifier):
        raise ValueError(
            f"Identifier '{identifier}' can only contain letters, digits, and underscores"
        )
    return identifier


def _serialize_value(value: Any) -> Any:
    """
    Serialize a Python value for MySQL storage.

    - dict/list -> JSON string
    - datetime -> ISO 8601 string
    - bool -> 0/1 integer
    - other types -> unchanged

    Args:
        value: The value to serialize.

    Returns:
        The serialized value suitable for MySQL.
    """
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


class _Txn:
    """The open transaction, shared by reference with any task that inherits it.

    A ContextVar copies its VALUE into each task created after the value was
    set. Storing an immutable `(owner, conn)` tuple therefore gave every such
    child task a snapshot that never expired: the parent's `set(None)` after
    commit changed only the parent's own context, so a child outliving the
    transaction kept being told it was inside one — and, under the write guard,
    was refused forever, naming a transaction that had ended long ago.

    Sharing one mutable holder fixes the lifetime: clearing `conn` is visible to
    parent and children alike, and `conn is None` means "no transaction" for
    everyone. `begin_transaction` must allocate a NEW holder every time — reusing
    one would resurrect a stale child's snapshot when the parent opens its next
    transaction, which is strictly harder to diagnose than what it replaced.
    """

    __slots__ = ("owner", "conn")

    def __init__(self, owner, conn) -> None:
        self.owner = owner
        self.conn = conn


class MySQLBackend(DatabaseBackend):
    """
    MySQL implementation of DatabaseBackend.

    Uses an aiomysql connection pool for high-concurrency async access.
    Transaction operations use a dedicated connection acquired from the pool.

    Transaction scope is the *asyncio task*, not the backend instance
    ------------------------------------------------------------------
    One backend instance serves every request on the event loop. Holding the
    open transaction's connection in an instance attribute therefore made it
    process-wide state: while any one caller sat inside `transaction()`, every
    OTHER concurrent caller's statement was silently routed onto that same
    connection instead of taking its own from the pool. Two coroutines then read
    the same socket, aiomysql raised "readexactly() called while another
    coroutine is already waiting for incoming data", the MySQL protocol stream
    desynced, and the connection died. Because the attribute was only cleared
    *after* a successful commit, the dead connection stayed installed and every
    subsequent statement in the process failed with
    `InterfaceError: (0, 'Not connected')` until the container was restarted.
    (Prod outage 2026-08-17 09:37–09:56; the trigger was a wide agent-data wipe,
    whose transaction holds the connection across hundreds of sequential
    deletes.)

    Binding the connection to a ContextVar fixes the blast radius: a value set
    inside a task is invisible to every other task, so a transaction can no
    longer capture unrelated callers' statements.

    Args:
        db_config: Dictionary with keys: host, port, user, password, database.
        pool_size: Maximum number of connections in the pool (default 10).
        pool_recycle: Connection recycle time in seconds (default 3600).
    """

    def __init__(
        self,
        db_config: Dict[str, Any],
        pool_size: int = 10,
        pool_recycle: int = 3600,
    ) -> None:
        self._db_config = db_config
        self._pool_size = pool_size
        self._pool_recycle = pool_recycle
        self._pool: Optional[aiomysql.Pool] = None
        # Per-instance ContextVar: the value is scoped to the calling task, so
        # concurrent requests never observe each other's transaction. Created in
        # __init__ rather than at module level so two backends (e.g. two
        # databases) cannot alias each other's transaction. Backends are
        # long-lived singletons — a handful per process — so this stays within
        # the "do not create ContextVars in hot paths" guidance.
        # The value is (owner_task, connection). The owner is recorded because
        # a ContextVar is COPIED into every task created after the value was
        # set — `asyncio.gather` / `create_task` inside a transaction body would
        # otherwise inherit the transaction connection and put several coroutines
        # back on one socket, which is the exact condition being fixed. Owner
        # identity turns that inheritance into either "use your own pooled
        # connection" (statements) or a loud error (commit/rollback).
        # Strong refs to in-flight pool wake-ups; see `_wake_pool_waiters`.
        self._wakeup_tasks: set["asyncio.Task"] = set()
        self._txn_conn: ContextVar[Optional[_Txn]] = ContextVar(
            f"mysql_txn_conn_{id(self):x}", default=None
        )

    # ===== Properties =====

    @property
    def placeholder(self) -> str:
        return "%s"

    @property
    def dialect(self) -> str:
        return "mysql"

    # ===== Lifecycle =====

    async def initialize(self) -> None:
        """
        Create the aiomysql connection pool.

        Configures UTF-8 charset and autocommit mode.
        """
        self._pool = await aiomysql.create_pool(
            host=self._db_config["host"],
            port=self._db_config.get("port", 3306),
            user=self._db_config["user"],
            password=self._db_config["password"],
            db=self._db_config["database"],
            minsize=1,
            maxsize=self._pool_size,
            pool_recycle=self._pool_recycle,
            autocommit=True,
            charset="utf8mb4",
        )

    async def close(self) -> None:
        """Close the connection pool and release all connections."""
        if self._pool is None:
            return

        # Only this task's transaction is reachable here — a ContextVar value
        # set in another task is by design invisible.
        conn = self._own_txn()
        if conn is not None:
            self._return_to_pool(conn, broken=True)
            self._clear_txn()

        self._pool.close()
        try:
            # `close()` only refuses NEW checkouts; `wait_closed()` then waits
            # for every already-checked-out connection to come back, and waits
            # forever if one never does. During shutdown that is the normal
            # case: cancelled requests make aiomysql close their connection on
            # the way out, and a closed connection is returned without waking
            # anyone. Bound the wait, then take the connections by force.
            await asyncio.wait_for(self._pool.wait_closed(), timeout=_POOL_CLOSE_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            logger.warning(
                "[MySQLBackend] pool did not drain in %ss — terminating "
                "outstanding connections", _POOL_CLOSE_TIMEOUT_SEC,
            )
            self._pool.terminate()
            await self._pool.wait_closed()
        self._pool = None

    def _own_txn(self) -> Optional[aiomysql.Connection]:
        """This task's transaction connection, or None.

        None also when the ContextVar holds a value INHERITED from the task that
        opened the transaction: that connection belongs to the parent, and a
        child using it is the two-coroutines-one-socket bug. Such callers fall
        through to `pool.acquire()` and get their own connection.
        """
        txn = self._txn_conn.get()
        if txn is None or txn.conn is None:
            return None
        return txn.conn if txn.owner is asyncio.current_task() else None

    def _reject_inherited_write(self) -> None:
        """Refuse a WRITE issued from a task that inherited someone's transaction.

        Such a write cannot join the transaction (that would be two coroutines
        on one socket), and letting it silently take a pooled connection is
        worse than failing: it autocommits, so the enclosing rollback does not
        undo it, and the caller is never told its write escaped the transaction
        it appeared to be inside.

        Reads are deliberately allowed through to the pool — `gather`-ing
        queries inside a transaction body is ordinary and harmless; they simply
        do not observe the uncommitted rows.
        """
        txn = self._txn_conn.get()
        # `txn.conn is None` means the transaction has ENDED. A child task that
        # outlives it must go back to writing normally, not stay locked out by a
        # snapshot of something that no longer exists.
        if txn is not None and txn.conn is not None and txn.owner is not asyncio.current_task():
            raise RuntimeError(
                "write issued from a task that inherited an enclosing transaction: "
                "it cannot join that transaction and would autocommit outside it "
                "(the enclosing rollback would not undo it). Do the write in the "
                "task that opened the transaction, or open a transaction here."
            )

    def _ensure_pool(self) -> aiomysql.Pool:
        """Return the pool, raising if not initialized."""
        if self._pool is None:
            raise RuntimeError("MySQLBackend is not initialized. Call initialize() first.")
        return self._pool

    # ===== Raw SQL Execution =====

    async def execute(
        self,
        query: str,
        params: Optional[tuple] = None,
    ) -> List[Dict[str, Any]]:
        """Execute a raw SQL query and return rows as dicts.

        Statement-level calls (no caller-owned transaction) are wrapped
        in `_retry_on_deadlock` so InnoDB errno 1213 is recovered
        transparently. Inside an explicit transaction the caller owns
        the boundary — we must NOT retry a single statement and leave
        earlier statements un-rolled-back.
        """
        pool = self._ensure_pool()

        txn = self._own_txn()
        if txn is not None:
            async with txn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(query, params or ())
                return await cursor.fetchall()

        async def _run():
            async with pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cursor:
                    await cursor.execute(query, params or ())
                    return await cursor.fetchall()

        return await _retry_on_deadlock(_run)

    async def execute_write(
        self,
        query: str,
        params: Optional[tuple] = None,
    ) -> int:
        """Execute a write SQL statement, returning affected row count.

        See `execute` for the retry-on-deadlock contract.
        """
        pool = self._ensure_pool()

        txn = self._own_txn()
        if txn is None:
            self._reject_inherited_write()
        if txn is not None:
            async with txn.cursor() as cursor:
                await cursor.execute(query, params or ())
                return cursor.rowcount

        async def _run():
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(query, params or ())
                    return cursor.rowcount

        return await _retry_on_deadlock(_run)

    # ===== CRUD Operations =====

    async def get(
        self,
        table: str,
        filters: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order_by: Optional[str] = None,
        fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Query rows from a table with filtering, pagination, and sorting."""
        safe_table = _validate_identifier(table)

        if fields:
            safe_fields = [_validate_identifier(f) for f in fields]
            columns = ", ".join(f"`{f}`" for f in safe_fields)
        else:
            columns = "*"

        query = f"SELECT {columns} FROM `{safe_table}`"
        params: list[Any] = []

        if filters:
            where_clauses = []
            for key, value in filters.items():
                safe_key = _validate_identifier(key)
                if value is None:
                    where_clauses.append(f"`{safe_key}` IS NULL")
                else:
                    where_clauses.append(f"`{safe_key}` = %s")
                    params.append(_serialize_value(value))
            query += " WHERE " + " AND ".join(where_clauses)

        if order_by:
            order_parts = order_by.split()
            safe_order_field = _validate_identifier(order_parts[0])
            direction = ""
            if len(order_parts) > 1 and order_parts[1].upper() in ("ASC", "DESC"):
                direction = " " + order_parts[1].upper()
            query += f" ORDER BY `{safe_order_field}`{direction}"

        if limit is not None:
            query += f" LIMIT {int(limit)}"
        if offset is not None:
            query += f" OFFSET {int(offset)}"

        return await self.execute(query, tuple(params))

    async def get_one(
        self,
        table: str,
        filters: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Query a single row matching the given filters."""
        results = await self.get(table, filters, limit=1)
        return results[0] if results else None

    async def get_by_ids(
        self,
        table: str,
        id_field: str,
        ids: List[Any],
        fields: Optional[List[str]] = None,
    ) -> List[Optional[Dict[str, Any]]]:
        """Batch-fetch rows by IDs, preserving input order.

        ``fields`` narrows the projection, same contract as ``get``. Use it for
        existence checks on tables with fat columns — `SELECT *` there drags the
        payload across the wire only to discard it. It must include ``id_field``
        or the order-preserving map below cannot be built.
        """
        if not ids:
            return []

        unique_ids = list(dict.fromkeys(ids))
        safe_table = _validate_identifier(table)
        safe_id_field = _validate_identifier(id_field)

        if fields:
            safe_fields = [_validate_identifier(f) for f in dict.fromkeys([*fields, id_field])]
            columns = ", ".join(f"`{f}`" for f in safe_fields)
        else:
            columns = "*"

        placeholders = ",".join(["%s"] * len(unique_ids))
        query = f"SELECT {columns} FROM `{safe_table}` WHERE `{safe_id_field}` IN ({placeholders})"

        results = await self.execute(query, tuple(unique_ids))

        result_map = {row[id_field]: row for row in results}
        return [result_map.get(id_val) for id_val in ids]

    async def insert(
        self,
        table: str,
        data: Dict[str, Any],
    ) -> int:
        """Insert a single row, returning the lastrowid."""
        if not data:
            raise ValueError("Insert data cannot be empty")

        safe_table = _validate_identifier(table)
        safe_keys = [_validate_identifier(key) for key in data.keys()]

        columns = ", ".join(f"`{key}`" for key in safe_keys)
        placeholders = ", ".join(["%s"] * len(data))
        query = f"INSERT INTO `{safe_table}` ({columns}) VALUES ({placeholders})"
        params = tuple(_serialize_value(v) for v in data.values())

        pool = self._ensure_pool()

        txn = self._own_txn()
        if txn is None:
            self._reject_inherited_write()
        if txn is not None:
            async with txn.cursor() as cursor:
                await cursor.execute(query, params)
                return cursor.lastrowid or 0
        else:
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(query, params)
                    return cursor.lastrowid or 0

    async def update(
        self,
        table: str,
        filters: Dict[str, Any],
        data: Dict[str, Any],
    ) -> int:
        """Update rows matching filters, returning the number of rows updated."""
        if not data:
            raise ValueError("Update data cannot be empty")
        if not filters:
            raise ValueError("Update operation must specify filter conditions")

        safe_table = _validate_identifier(table)

        set_clauses = []
        params: list[Any] = []
        for key, value in data.items():
            safe_key = _validate_identifier(key)
            set_clauses.append(f"`{safe_key}` = %s")
            params.append(_serialize_value(value))

        where_clauses = []
        for key, value in filters.items():
            safe_key = _validate_identifier(key)
            if value is None:
                where_clauses.append(f"`{safe_key}` IS NULL")
            else:
                where_clauses.append(f"`{safe_key}` = %s")
                params.append(_serialize_value(value))

        query = (
            f"UPDATE `{safe_table}` "
            f"SET {', '.join(set_clauses)} "
            f"WHERE {' AND '.join(where_clauses)}"
        )

        pool = self._ensure_pool()

        txn = self._own_txn()
        if txn is None:
            self._reject_inherited_write()
        if txn is not None:
            async with txn.cursor() as cursor:
                await cursor.execute(query, tuple(params))
                return cursor.rowcount
        else:
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(query, tuple(params))
                    return cursor.rowcount

    async def delete(
        self,
        table: str,
        filters: Dict[str, Any],
    ) -> int:
        """Delete rows matching filters, returning the number of rows deleted."""
        if not filters:
            raise ValueError("Delete operation must specify filter conditions")

        safe_table = _validate_identifier(table)

        where_clauses = []
        params: list[Any] = []
        for key, value in filters.items():
            safe_key = _validate_identifier(key)
            if value is None:
                where_clauses.append(f"`{safe_key}` IS NULL")
            else:
                where_clauses.append(f"`{safe_key}` = %s")
                params.append(_serialize_value(value))

        query = f"DELETE FROM `{safe_table}` WHERE {' AND '.join(where_clauses)}"

        pool = self._ensure_pool()

        txn = self._own_txn()
        if txn is None:
            self._reject_inherited_write()
        if txn is not None:
            async with txn.cursor() as cursor:
                await cursor.execute(query, tuple(params))
                return cursor.rowcount
        else:
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(query, tuple(params))
                    return cursor.rowcount

    async def upsert(
        self,
        table: str,
        data: Dict[str, Any],
        id_field: str,
    ) -> int:
        """
        Insert or update using INSERT ... ON DUPLICATE KEY UPDATE.

        Uses MySQL 8.0.20+ AS new_row syntax.

        Args:
            table: Table name.
            data: Column-value pairs to insert/update.
            id_field: The unique/primary key column for conflict detection.

        Returns:
            Number of affected rows (1=new insert, 2=updated existing).
        """
        if not data:
            raise ValueError("Insert data cannot be empty")

        safe_table = _validate_identifier(table)
        safe_keys = [_validate_identifier(key) for key in data.keys()]
        safe_id_field = _validate_identifier(id_field)

        columns = ", ".join(f"`{key}`" for key in safe_keys)
        placeholders = ", ".join(["%s"] * len(data))

        # Build ON DUPLICATE KEY UPDATE clause (excluding the id field)
        update_clauses = []
        for key in safe_keys:
            if key != safe_id_field:
                update_clauses.append(f"`{key}` = new_row.`{key}`")

        query = f"INSERT INTO `{safe_table}` ({columns}) VALUES ({placeholders}) AS new_row"
        if update_clauses:
            query += f" ON DUPLICATE KEY UPDATE {', '.join(update_clauses)}"

        params = tuple(_serialize_value(v) for v in data.values())

        pool = self._ensure_pool()

        txn = self._own_txn()
        if txn is None:
            self._reject_inherited_write()
        if txn is not None:
            async with txn.cursor() as cursor:
                await cursor.execute(query, params)
                return cursor.rowcount
        else:
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(query, params)
                    return cursor.rowcount

    async def probe(self) -> None:
        """`SELECT 1` on a pooled connection, closed rather than recycled if the
        probe is cancelled.

        The default implementation would go through `execute`, whose
        `async with pool.acquire()` returns the connection unconditionally —
        and aiomysql only discards it if it is already closed, which
        `CancelledError` does not do. The result would be a possibly desynced
        connection back on the free list.

        That matters because of WHEN it happens: the caller's timeout fires
        exactly when the database is slow, so the container healthcheck would
        reproduce it every 30 seconds for the duration of a slowdown. Each
        timeout could poison one more pooled connection, and the next real
        request to borrow it would fail the same way the 2026-08-17 outage did.
        A probe added to make failure visible must not manufacture it.
        """
        pool = self._ensure_pool()
        conn = await pool.acquire()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute("SELECT 1")
        except BaseException:
            # BaseException, not Exception: CancelledError is the case this
            # whole method exists for.
            self._return_to_pool(conn, broken=True)
            raise
        else:
            self._return_to_pool(conn, broken=False)

    # ===== Transaction Support =====

    def _return_to_pool(self, conn: aiomysql.Connection, *, broken: bool) -> None:
        """Hand a transaction connection back to the pool.

        `broken=True` closes the socket first. A connection whose commit or
        rollback raised may have a desynced protocol stream: releasing it as-is
        would put it back on the free list, where the next unrelated caller
        would inherit the corruption. aiomysql's `release()` drops a closed
        connection instead of reusing it, so closing first is what turns
        "poison the pool" into "lose one connection".

        The explicit wake-up is not redundant. In aiomysql 0.3.2, `Pool.release`
        schedules its own `_wakeup()` only inside `if not conn.closed:` — so
        returning a CLOSED connection frees the slot without ever notifying the
        waiters blocked in `acquire()`. With the pool saturated and every commit
        failing (exactly the incident this file now guards against), all returns
        take this path and queued requests would sleep until some unrelated
        healthy connection happened to be released: a hang instead of a fast
        error. Verified against aiomysql 0.3.2; re-check on upgrade.
        """
        if broken and not conn.closed:
            conn.close()
        pool = self._pool
        if pool is None:
            # No pool to return it to (closed already). Still close it, or the
            # socket leaks for the life of the process.
            if not conn.closed:
                conn.close()
            return
        pool.release(conn)
        if broken:
            self._wake_pool_waiters(pool)

    def _wake_pool_waiters(self, pool: aiomysql.Pool) -> None:
        # `_wakeup` is private to aiomysql. Reached via getattr because this runs
        # from an `except BaseException:` path: after an upgrade renames it, a
        # bare attribute access would raise AttributeError and REPLACE the real
        # database error the caller is about to see.
        wakeup = getattr(pool, "_wakeup", None)
        if wakeup is None:  # pragma: no cover - only after an aiomysql upgrade
            logger.warning(
                "[MySQLBackend] aiomysql has no _wakeup(); pool waiters may stall "
                "after a broken connection is returned. Re-check the release path."
            )
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - no loop during teardown
            return
        # Keep a reference: a bare create_task can be garbage-collected before
        # it runs (a lesson this repo has already paid for once).
        task = loop.create_task(wakeup())
        self._wakeup_tasks.add(task)
        task.add_done_callback(self._on_wakeup_done)

    def _on_wakeup_done(self, task: "asyncio.Task") -> None:
        self._wakeup_tasks.discard(task)
        # M2: read the result, or a failure surfaces later as the unhelpful
        # "Task exception was never retrieved" during GC.
        if not task.cancelled() and task.exception() is not None:
            logger.warning(f"[MySQLBackend] pool wake-up failed — {task.exception()}")

    def _clear_txn(self) -> None:
        """End the transaction for the owner AND for every task that inherited it.

        Mutating the shared holder rather than `set(None)`: the latter would only
        rewrite this task's own context, leaving children convinced a finished
        transaction was still open.
        """
        txn = self._txn_conn.get()
        if txn is not None:
            txn.conn = None
        self._txn_conn.set(None)

    def _owned_or_raise(self) -> aiomysql.Connection:
        """The connection this task may commit/roll back, or an explanation."""
        txn = self._txn_conn.get()
        if txn is None or txn.conn is None:
            raise RuntimeError("No active transaction")
        conn = txn.conn
        if txn.owner is not asyncio.current_task():
            # Ending someone else's transaction would release a connection the
            # parent task is still writing to — the parent's next statement
            # would land on a connection already handed to an unrelated caller,
            # and its own commit would double-release. Refuse loudly.
            raise RuntimeError(
                "No active transaction in this task (the enclosing transaction "
                "belongs to the task that opened it; do not commit/rollback it "
                "from a child task)"
            )
        return conn

    async def begin_transaction(self) -> None:
        """Begin a transaction on a connection dedicated to the calling task.

        A transaction inherited from a parent task does NOT block this: the
        child gets its own connection and its own transaction, and setting the
        ContextVar here is invisible to the parent.
        """
        if self._own_txn() is not None:
            raise RuntimeError("Already in a transaction")

        pool = self._ensure_pool()
        conn = await pool.acquire()
        try:
            await conn.begin()
        except BaseException:
            # BEGIN failed — the caller gets the exception and will never call
            # commit/rollback, so release here or the connection leaks.
            self._return_to_pool(conn, broken=True)
            raise
        # A fresh holder each time — see `_Txn`.
        self._txn_conn.set(_Txn(asyncio.current_task(), conn))

    async def commit(self) -> None:
        """Commit the current transaction and release the connection.

        The ContextVar is cleared in `finally`: if COMMIT raises, the connection
        is already unusable, and leaving it installed would route every later
        statement in this task onto a dead socket.
        """
        conn = self._owned_or_raise()

        try:
            await conn.commit()
        except BaseException:
            self._return_to_pool(conn, broken=True)
            raise
        else:
            self._return_to_pool(conn, broken=False)
        finally:
            self._clear_txn()

    async def rollback(self) -> None:
        """Roll back the current transaction and release the connection.

        See `commit` for why the ContextVar is cleared unconditionally.
        """
        conn = self._owned_or_raise()

        try:
            await conn.rollback()
        except BaseException:
            self._return_to_pool(conn, broken=True)
            raise
        else:
            self._return_to_pool(conn, broken=False)
        finally:
            self._clear_txn()
