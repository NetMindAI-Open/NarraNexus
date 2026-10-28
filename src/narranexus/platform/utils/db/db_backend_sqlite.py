"""
@file_name: db_backend_sqlite.py
@author: NexusAgent
@date: 2026-04-02
@description: SQLite implementation of the DatabaseBackend interface

Uses aiosqlite for async SQLite access. Designed for local/desktop use
(Tauri 2 migration) with WAL journal mode for concurrent read support.

Key design decisions:
- Single long-lived connection (not a pool) since SQLite is file-based
- asyncio.Lock for write serialization (SQLite allows only one writer)
- WAL mode enables concurrent readers even during writes
- JSON/dict/list values serialized to JSON strings for storage
- Boolean values stored as 0/1 integers
- datetime values stored as ISO 8601 strings
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiosqlite
from loguru import logger

from narranexus.platform.utils.db.db_backend import DatabaseBackend


# =============================================================================
# aiosqlite worker-thread hardening
# =============================================================================
#
# aiosqlite runs every connection on its own worker thread and hands results
# back with `future.get_loop().call_soon_threadsafe(...)`. If the loop that
# issued the statement has been closed in the meantime, that call raises
# RuntimeError — and upstream's handler answers by making the SAME call again
# from its `except` block, which raises again, uncaught. The worker thread
# then DIES, silently, still owning an open sqlite3 connection.
#
# Nothing reclaims that connection. It keeps its file handle and whatever lock
# the abandoned statement held, so every later writer waits out
# `busy_timeout` (30s) on each of `_MAX_WRITE_RETRIES` (10) attempts —
# ~5 minutes of blocking per collision, then "database is locked".
#
# A closed loop is not exotic here. The trigger is a SHORT-LIVED loop, and this
# codebase mints them on purpose:
#   * `get_db_client_sync()` builds its client inside `asyncio.run(...)`, whose
#     loop is dead the instant it returns (`db_factory.py`). `ContextRuntime`
#     takes that path whenever no client is injected, and `module_runner.py`
#     already carries two comments about the fallout.
#   * `lark_trigger.py` makes a fresh loop per WS reconnect.
#   * The test harness, and any one-shot script or migration.
# A fire-and-forget DB task caught by its loop's shutdown is the other half
# (`schedule_user_no_quota_rearm` is one) — though that one rides the uvicorn
# main loop, so it only bites at server shutdown.
#
# NOT the MCP container: `module_runner.py` gives each module its own PROCESS
# with one process-lifetime `asyncio.run`, no threaded loops. An earlier draft
# of this comment claimed otherwise and would have sent the next reader into
# MCP code to look for something that is not there.
#
# So: keep the thread alive. If the awaiting coroutine's loop is gone, the
# result has no one to go to and dropping it is correct — dying is not, because
# this thread is the only one that can ever close the connection. This is the
# project's incident lesson #2 (third-party fire-and-forget is a mine too)
# applied to aiosqlite, and it is narrow on purpose: only the delivery step is
# replaced, the queue protocol and the STOP sentinel are upstream's.
def _deliver_to_origin_loop(future, setter, value) -> None:
    """Hand a result/exception back to the loop that asked for it, if it is
    still there. A closed loop means the awaiting coroutine died with it."""
    if future is None:
        return
    loop = future.get_loop()
    try:
        loop.call_soon_threadsafe(setter, future, value)
    except RuntimeError:
        # A CLOSED loop is the expected case and stays quiet: the coroutine that
        # was awaiting this died with its loop, so there is nobody to tell.
        #
        # A LIVE loop refusing the callback is a different animal, and silence
        # there would be the alarm-disabling this codebase has been bitten by
        # (incident lesson #3: a filter must be precise to a class AND a
        # context). The awaiting `await future` in aiosqlite has no timeout of
        # its own, so an undelivered result is a permanent, silent stall on a DB
        # call — the same invisible-symptom shape this whole patch exists to
        # remove. Upstream at least died loudly.
        if not loop.is_closed():
            logger.warning(
                f"aiosqlite result dropped on a LIVE loop {loop!r} — the "
                f"awaiting DB call will never resolve"
            )


def _resilient_connection_worker_thread(tx) -> None:
    """Drop-in for `aiosqlite.core._connection_worker_thread` whose result
    delivery cannot kill the thread. Same protocol, same stop sentinel, and the
    same three debug lines — anyone who turns the `aiosqlite` logger up to DEBUG
    to chase a connection-layer problem should still get their trace, for every
    connection in the process."""
    while True:
        future, function = tx.get()
        try:
            aiosqlite.core.LOG.debug("executing %s", function)
            result = function()
            _deliver_to_origin_loop(future, aiosqlite.core.set_result, result)
            aiosqlite.core.LOG.debug("operation %s completed", function)
            if result is aiosqlite.core._STOP_RUNNING_SENTINEL:
                break
        except BaseException as e:  # noqa: B036 — mirrors upstream's contract
            aiosqlite.core.LOG.debug("returning exception %s", e)
            _deliver_to_origin_loop(future, aiosqlite.core.set_exception, e)


# The worker must also be a DAEMON thread, and the two halves are one fix.
#
# Upstream creates it with a bare `Thread(target=...)`, i.e. daemon=False, and
# relied — by accident — on the delivery crash above to reap orphans: a worker
# that dies lets the process exit. Keeping it alive without this half converts
# "5 minutes of blocked writes" into "the process never exits at all",
# because `Py_FinalizeEx` runs `threading._shutdown()` (which JOINS non-daemon
# threads) BEFORE module teardown, so `Connection.__del__ -> stop()` never gets
# its chance. Measured on 2026-08-17 with a connection orphaned behind a
# `BEGIN IMMEDIATE`: upstream exits in 1.4s, delivery-patch-only hangs forever.
#
# That regression would have landed on the desktop build (SQLite IS the desktop
# backend, binding rule #7) as "quit leaves a zombie holding the DB file", and
# in containers as a SIGKILL on every `docker stop`. Strictly harder to diagnose
# than the slowness it replaced.
#
# Daemon status can only be set before `start()`, and aiosqlite starts the
# thread in `Connection.__await__` — so it has to happen in the constructor.
# Setting it in `SQLiteBackend.initialize()` after `await aiosqlite.connect()`
# raises "cannot set daemon status of active thread".
#
# This does NOT license a weaker shutdown path: a daemon worker can be killed
# mid-write at exit (safe — WAL is crash-safe, same as SIGKILL), but the
# ORDINARY close must stay explicit. `close_db_client()` and the suite's
# `pytest_sessionfinish` are still load-bearing.
# Assigning to an attribute upstream has renamed away succeeds SILENTLY, which
# would turn both halves of this hardening into a no-op and bring the 5-minute
# lock class back with a green suite. Fail at import instead — the version floor
# in pyproject.toml rules out OLDER releases that never had these names; only
# this check can catch a NEWER one that renames them, which is why it exists.
# Two tests additionally assert both patches are installed.
# The complete internal surface this module touches, in three kinds — say it this
# way and the next person adding an `aiosqlite.core.X` reference knows which
# bucket theirs falls in:
#   * resolved at call time by `_resilient_connection_worker_thread`: `LOG`,
#     `set_result`, `_STOP_RUNNING_SENTINEL`, `set_exception`;
#   * the ASSIGNMENT target it replaces: `_connection_worker_thread`, never read
#     here, and guarded for the reason the paragraph above gives;
#   * the class whose constructor gets wrapped: `Connection`.
#
# `Connection.__init__` is deliberately NOT in this tuple: `hasattr(cls,
# "__init__")` is true for every class in Python, so such a check is a tautology
# (one was here and was deleted). Its real guard is the `hasattr(self, "_thread")`
# check inside `_daemon_worker_connection_init`.
#
# `set_result` / `set_exception` are the ones that must not be missed: they are
# READS, so a rename does not degrade quietly — it raises AttributeError inside
# the worker's `try`, lands in `except BaseException`, and the handler's own
# `aiosqlite.core.set_exception` raises again with nobody left to catch it. The
# worker thread dies holding an open connection, which is the exact failure this
# whole module exists to remove, now as a daemon thread and therefore quieter.
for _required in (
    "_connection_worker_thread",
    "_STOP_RUNNING_SENTINEL",
    "LOG",
    "set_result",
    "set_exception",
    # The class this module reaches into to wrap its constructor. Losing it is a
    # bare AttributeError at the patch site rather than the actionable ImportError
    # promised above. Unlike `Connection.__init__`, whose presence is guaranteed
    # by inheritance, `Connection` itself can genuinely disappear.
    "Connection",
):
    if not hasattr(aiosqlite.core, _required):
        raise ImportError(
            f"aiosqlite.core.{_required} is gone — the worker-thread hardening in "
            f"{__name__} targets aiosqlite internals and must be re-checked "
            f"against the installed version before this module can be trusted"
        )
_upstream_connection_init = aiosqlite.core.Connection.__init__


def _daemon_worker_connection_init(self, *args, **kwargs) -> None:
    _upstream_connection_init(self, *args, **kwargs)
    if not hasattr(self, "_thread"):  # pragma: no cover — upstream rename
        raise RuntimeError(
            "aiosqlite Connection has no ._thread — the daemon half of the "
            "worker hardening cannot be applied; see db_backend_sqlite"
        )
    self._thread.daemon = True


aiosqlite.core._connection_worker_thread = _resilient_connection_worker_thread
aiosqlite.core.Connection.__init__ = _daemon_worker_connection_init


# Regex for ISO 8601 timestamp detection (covers common SQLite datetime formats)
_ISO_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
)

# Column name suffixes that indicate timestamp fields
_TIMESTAMP_SUFFIXES = (
    "_at", "_time", "created_at", "updated_at", "completed_at",
    "archived_at", "last_used_at", "registered_at", "last_seen_at",
    "joined_at", "last_read_at", "last_processed_at", "last_retry_at",
    "last_login_time", "create_time", "update_time", "agent_create_time",
    "agent_update_time", "linked_at", "unlinked_at",
)


def _try_parse_timestamp(value: str) -> Any:
    """Try to parse an ISO 8601 timestamp string into a datetime object."""
    cleaned = value.rstrip("Z")
    try:
        from datetime import timezone
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return value


def _auto_parse_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Auto-convert timestamp strings in a SQLite row to datetime objects.

    SQLite stores all timestamps as TEXT. This function detects timestamp
    columns by name suffix and value format, and converts them to Python
    datetime objects so the rest of the codebase can call .strftime(),
    .tzinfo, etc. without errors.
    """
    for key, value in row.items():
        if value is None or not isinstance(value, str):
            continue
        # Only parse columns with known timestamp suffixes (safe, no false positives)
        if any(key.endswith(suffix) for suffix in _TIMESTAMP_SUFFIXES):
            row[key] = _try_parse_timestamp(value)
    return row


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
    Serialize a Python value for SQLite storage.

    - dict/list -> JSON string
    - datetime -> ISO 8601 string
    - bool -> 0/1 integer
    - other types -> unchanged

    Args:
        value: The value to serialize.

    Returns:
        The serialized value suitable for SQLite.
    """
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


class SQLiteBackend(DatabaseBackend):
    """
    SQLite implementation of DatabaseBackend.

    Uses a single long-lived aiosqlite connection with WAL journal mode
    for concurrent read support. Write operations are serialized via
    an asyncio.Lock.

    Args:
        db_path: Path to the SQLite database file, or ':memory:' for in-memory.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None
        self._write_lock = asyncio.Lock()
        self._in_transaction = False

    # ===== Properties =====

    @property
    def placeholder(self) -> str:
        return "?"

    @property
    def dialect(self) -> str:
        return "sqlite"

    # ===== Lifecycle =====

    async def initialize(self) -> None:
        """
        Open the SQLite connection and configure PRAGMAs.

        Enables WAL mode, sets performance-related PRAGMAs, and
        enables foreign key enforcement.
        """
        # The DB lives at ~/.narranexus/nexus.db. If that parent dir is
        # unwritable, aiosqlite fails with a cryptic "unable to open database
        # file" and (before the readiness gate) took the proxy/backend down on
        # startup → "Connection failed". Unlike logs, the DB CANNOT be diverted
        # elsewhere (it's the user's data), so: repair the perms if we own the
        # dir, else raise a clear, actionable error naming the fix. ":memory:"
        # has no parent and is skipped.
        if self._db_path not in (":memory:", "") and not self._db_path.startswith("file::memory:"):
            from pathlib import Path
            from narranexus.platform.utils.fs_safety import ensure_writable_dir, chown_hint

            parent = Path(self._db_path).expanduser().parent
            if parent and str(parent) not in ("", ".") and not ensure_writable_dir(parent):
                raise RuntimeError(
                    f"SQLite database directory is not writable: {parent}\n"
                    f"This is usually a stale ~/.narranexus owned by another user "
                    f"(e.g. carried over by Migration Assistant from another Mac). "
                    f"Fix it with:  {chown_hint(parent)}"
                )

        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row

        # Configure PRAGMAs for performance and correctness
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA cache_size=-64000")  # 64MB
        await self._conn.execute("PRAGMA mmap_size=268435456")  # 256MB
        await self._conn.execute("PRAGMA temp_store=MEMORY")
        await self._conn.execute("PRAGMA busy_timeout=30000")  # 30s — generous wait for multi-process writes
        await self._conn.execute("PRAGMA foreign_keys=ON")

    async def close(self) -> None:
        """Close the SQLite connection."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    def _ensure_conn(self) -> aiosqlite.Connection:
        """Return the connection, raising if not initialized."""
        if self._conn is None:
            raise RuntimeError("SQLiteBackend is not initialized. Call initialize() first.")
        return self._conn

    # ===== Write Retry Helper =====

    _MAX_WRITE_RETRIES: int = 10
    _BASE_BACKOFF: float = 0.2  # seconds
    _MAX_JITTER: float = 0.3    # seconds

    async def _retry_write(self, fn, description: str = "write") -> Any:
        """Execute a write callable with retry on 'database is locked'.

        All write operations (execute_write, insert, update, delete, upsert)
        funnel through this method so that cross-process SQLite lock contention
        is handled uniformly.

        Args:
            fn: An async callable that performs the actual write under _write_lock.
            description: Human-readable label for log messages.

        Returns:
            Whatever *fn* returns (rowcount, lastrowid, etc.).
        """
        import random
        for attempt in range(self._MAX_WRITE_RETRIES):
            try:
                async with self._write_lock:
                    return await fn()
            except Exception as e:
                if "database is locked" in str(e) and attempt < self._MAX_WRITE_RETRIES - 1:
                    wait = self._BASE_BACKOFF * (2 ** min(attempt, 4)) + random.uniform(0, self._MAX_JITTER)
                    logger.warning(
                        f"SQLite {description} locked (attempt {attempt + 1}/{self._MAX_WRITE_RETRIES}), "
                        f"retrying in {wait:.2f}s"
                    )
                    await asyncio.sleep(wait)
                else:
                    raise

    # ===== Raw SQL Execution =====

    async def execute(
        self,
        query: str,
        params: Optional[tuple] = None,
    ) -> List[Dict[str, Any]]:
        """Execute a raw SQL query and return rows as dicts."""
        conn = self._ensure_conn()
        cursor = await conn.execute(query, params or ())
        rows = await cursor.fetchall()
        if rows:
            columns = [desc[0] for desc in cursor.description]
            return [_auto_parse_row(dict(zip(columns, row))) for row in rows]
        return []

    async def execute_write(
        self,
        query: str,
        params: Optional[tuple] = None,
        _max_retries: int = 10,
    ) -> int:
        """Execute a write SQL statement, returning affected row count."""
        conn = self._ensure_conn()

        async def _do_write():
            cursor = await conn.execute(query, params or ())
            if not self._in_transaction:
                await conn.commit()
            return cursor.rowcount

        return await self._retry_write(_do_write, description="execute_write")

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
            columns = ", ".join(f'"{f}"' for f in safe_fields)
        else:
            columns = "*"

        query = f'SELECT {columns} FROM "{safe_table}"'
        params: list[Any] = []

        if filters:
            where_clauses = []
            for key, value in filters.items():
                safe_key = _validate_identifier(key)
                if value is None:
                    where_clauses.append(f'"{safe_key}" IS NULL')
                else:
                    where_clauses.append(f'"{safe_key}" = ?')
                    params.append(_serialize_value(value))
            query += " WHERE " + " AND ".join(where_clauses)

        if order_by:
            order_parts = order_by.split()
            safe_order_field = _validate_identifier(order_parts[0])
            direction = ""
            if len(order_parts) > 1 and order_parts[1].upper() in ("ASC", "DESC"):
                direction = " " + order_parts[1].upper()
            query += f' ORDER BY "{safe_order_field}"{direction}'

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
            columns = ", ".join(f'"{f}"' for f in safe_fields)
        else:
            columns = "*"

        placeholders = ",".join(["?"] * len(unique_ids))
        query = f'SELECT {columns} FROM "{safe_table}" WHERE "{safe_id_field}" IN ({placeholders})'

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

        columns = ", ".join(f'"{key}"' for key in safe_keys)
        placeholders = ", ".join(["?"] * len(data))
        query = f'INSERT INTO "{safe_table}" ({columns}) VALUES ({placeholders})'
        params = tuple(_serialize_value(v) for v in data.values())

        conn = self._ensure_conn()

        async def _do_insert():
            cursor = await conn.execute(query, params)
            if not self._in_transaction:
                await conn.commit()
            return cursor.lastrowid or 0

        return await self._retry_write(_do_insert, description=f"insert({safe_table})")

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
            set_clauses.append(f'"{safe_key}" = ?')
            params.append(_serialize_value(value))

        where_clauses = []
        for key, value in filters.items():
            safe_key = _validate_identifier(key)
            if value is None:
                where_clauses.append(f'"{safe_key}" IS NULL')
            else:
                where_clauses.append(f'"{safe_key}" = ?')
                params.append(_serialize_value(value))

        query = (
            f'UPDATE "{safe_table}" '
            f'SET {", ".join(set_clauses)} '
            f'WHERE {" AND ".join(where_clauses)}'
        )

        conn = self._ensure_conn()
        final_params = tuple(params)

        async def _do_update():
            cursor = await conn.execute(query, final_params)
            if not self._in_transaction:
                await conn.commit()
            return cursor.rowcount

        return await self._retry_write(_do_update, description=f"update({safe_table})")

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
                where_clauses.append(f'"{safe_key}" IS NULL')
            else:
                where_clauses.append(f'"{safe_key}" = ?')
                params.append(_serialize_value(value))

        query = f'DELETE FROM "{safe_table}" WHERE {" AND ".join(where_clauses)}'

        conn = self._ensure_conn()
        final_params = tuple(params)

        async def _do_delete():
            cursor = await conn.execute(query, final_params)
            if not self._in_transaction:
                await conn.commit()
            return cursor.rowcount

        return await self._retry_write(_do_delete, description=f"delete({safe_table})")

    async def upsert(
        self,
        table: str,
        data: Dict[str, Any],
        id_field: str,
    ) -> int:
        """
        Insert or update using INSERT ... ON CONFLICT DO UPDATE.

        Args:
            table: Table name.
            data: Column-value pairs to insert/update.
            id_field: The unique/primary key column for conflict detection.

        Returns:
            Number of affected rows.
        """
        if not data:
            raise ValueError("Insert data cannot be empty")

        safe_table = _validate_identifier(table)
        safe_keys = [_validate_identifier(key) for key in data.keys()]
        safe_id_field = _validate_identifier(id_field)

        columns = ", ".join(f'"{key}"' for key in safe_keys)
        placeholders = ", ".join(["?"] * len(data))

        # Build ON CONFLICT ... DO UPDATE SET clause (excluding the id field)
        update_clauses = []
        for key in safe_keys:
            if key != safe_id_field:
                update_clauses.append(f'"{key}" = excluded."{key}"')

        query = f'INSERT INTO "{safe_table}" ({columns}) VALUES ({placeholders})'
        if update_clauses:
            query += f' ON CONFLICT("{safe_id_field}") DO UPDATE SET {", ".join(update_clauses)}'

        params = tuple(_serialize_value(v) for v in data.values())

        conn = self._ensure_conn()

        async def _do_upsert():
            cursor = await conn.execute(query, params)
            if not self._in_transaction:
                await conn.commit()
            return cursor.rowcount

        return await self._retry_write(_do_upsert, description=f"upsert({safe_table})")

    # ===== Transaction Support =====

    async def begin_transaction(self) -> None:
        """Begin a transaction by executing BEGIN."""
        if self._in_transaction:
            raise RuntimeError("Already in a transaction")
        conn = self._ensure_conn()
        await conn.execute("BEGIN")
        self._in_transaction = True

    async def commit(self) -> None:
        """Commit the current transaction."""
        if not self._in_transaction:
            raise RuntimeError("No active transaction")
        conn = self._ensure_conn()
        await conn.commit()
        self._in_transaction = False

    async def rollback(self) -> None:
        """Rollback the current transaction."""
        if not self._in_transaction:
            raise RuntimeError("No active transaction")
        conn = self._ensure_conn()
        await conn.rollback()
        self._in_transaction = False
