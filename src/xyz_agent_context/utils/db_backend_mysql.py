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
- Transaction support using a dedicated connection from the pool
- IS NULL handling for None filter values in get/update/delete
- JSON/dict/list values serialized to JSON strings for storage
- Boolean values stored as 0/1 integers
- datetime values stored as ISO 8601 strings
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiomysql

from xyz_agent_context.utils.db_backend import DatabaseBackend


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


class MySQLBackend(DatabaseBackend):
    """
    MySQL implementation of DatabaseBackend.

    Uses an aiomysql connection pool for high-concurrency async access.
    Transaction operations use a dedicated connection acquired from the pool.

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
        self._transaction_connection: Optional[aiomysql.Connection] = None

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

        if self._transaction_connection is not None:
            self._pool.release(self._transaction_connection)
            self._transaction_connection = None

        self._pool.close()
        await self._pool.wait_closed()
        self._pool = None

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
        """Execute a raw SQL query and return rows as dicts."""
        pool = self._ensure_pool()

        if self._transaction_connection is not None:
            async with self._transaction_connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(query, params or ())
                return await cursor.fetchall()
        else:
            async with pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cursor:
                    await cursor.execute(query, params or ())
                    return await cursor.fetchall()

    async def execute_write(
        self,
        query: str,
        params: Optional[tuple] = None,
    ) -> int:
        """Execute a write SQL statement, returning affected row count."""
        pool = self._ensure_pool()

        if self._transaction_connection is not None:
            async with self._transaction_connection.cursor() as cursor:
                await cursor.execute(query, params or ())
                return cursor.rowcount
        else:
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(query, params or ())
                    return cursor.rowcount

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
        ids: List[str],
    ) -> List[Optional[Dict[str, Any]]]:
        """Batch-fetch rows by IDs, preserving input order."""
        if not ids:
            return []

        unique_ids = list(dict.fromkeys(ids))
        safe_table = _validate_identifier(table)
        safe_id_field = _validate_identifier(id_field)

        placeholders = ",".join(["%s"] * len(unique_ids))
        query = f"SELECT * FROM `{safe_table}` WHERE `{safe_id_field}` IN ({placeholders})"

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

        if self._transaction_connection is not None:
            async with self._transaction_connection.cursor() as cursor:
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

        if self._transaction_connection is not None:
            async with self._transaction_connection.cursor() as cursor:
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

        if self._transaction_connection is not None:
            async with self._transaction_connection.cursor() as cursor:
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

        if self._transaction_connection is not None:
            async with self._transaction_connection.cursor() as cursor:
                await cursor.execute(query, params)
                return cursor.rowcount
        else:
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(query, params)
                    return cursor.rowcount

    # ===== Transaction Support =====

    async def begin_transaction(self) -> None:
        """Begin a transaction by acquiring a dedicated connection."""
        if self._transaction_connection is not None:
            raise RuntimeError("Already in a transaction")

        pool = self._ensure_pool()
        self._transaction_connection = await pool.acquire()
        await self._transaction_connection.begin()

    async def commit(self) -> None:
        """Commit the current transaction and release the connection."""
        if self._transaction_connection is None:
            raise RuntimeError("No active transaction")

        pool = self._ensure_pool()
        await self._transaction_connection.commit()
        pool.release(self._transaction_connection)
        self._transaction_connection = None

    async def rollback(self) -> None:
        """Rollback the current transaction and release the connection."""
        if self._transaction_connection is None:
            raise RuntimeError("No active transaction")

        pool = self._ensure_pool()
        await self._transaction_connection.rollback()
        pool.release(self._transaction_connection)
        self._transaction_connection = None
