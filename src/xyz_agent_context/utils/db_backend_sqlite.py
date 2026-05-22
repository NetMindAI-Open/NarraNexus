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

from xyz_agent_context.utils.db_backend import DatabaseBackend


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
            from xyz_agent_context.utils.fs_safety import ensure_writable_dir, chown_hint

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
        ids: List[str],
    ) -> List[Optional[Dict[str, Any]]]:
        """Batch-fetch rows by IDs, preserving input order."""
        if not ids:
            return []

        unique_ids = list(dict.fromkeys(ids))
        safe_table = _validate_identifier(table)
        safe_id_field = _validate_identifier(id_field)

        placeholders = ",".join(["?"] * len(unique_ids))
        query = f'SELECT * FROM "{safe_table}" WHERE "{safe_id_field}" IN ({placeholders})'

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
