"""
@file_name: database.py
@author: NetMind.AI
@date: 2025-11-28
@description: Truly asynchronous database client with pluggable backend support

This is the project's main database client. It supports two modes:
1. Direct aiomysql mode (legacy) - uses aiomysql for native async I/O
2. Backend-delegated mode - delegates all operations to a DatabaseBackend instance
   (e.g., SQLiteBackend for local/desktop, MySQLBackend for cloud)

When a DatabaseBackend is provided via create_with_backend(), all CRUD and
transaction operations are delegated to it. When no backend is provided,
the client falls back to the original aiomysql code path.

Usage examples:
    # Legacy MySQL mode
    db = await AsyncDatabaseClient.create()

    # Backend-delegated mode (SQLite)
    from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend
    backend = SQLiteBackend("/path/to/db.sqlite")
    await backend.initialize()
    db = await AsyncDatabaseClient.create_with_backend(backend)

    # Interface is identical regardless of mode
    await db.insert("chat_history", {"message": "Hello"})
    await db.get("chat_history", {"agent_id": "agent_1"})
"""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional, Type, TypeVar, TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

import aiomysql
from loguru import logger
from pydantic import BaseModel

if TYPE_CHECKING:
    from xyz_agent_context.utils.db_backend import DatabaseBackend

T = TypeVar('T', bound=BaseModel)


def parse_database_url(url: str) -> Dict[str, Any]:
    """Parse a mysql:// format URL into database connection parameters"""
    parsed = urlparse(url)
    if parsed.scheme not in {"mysql", "mysql+mysqlconnector"}:
        raise ValueError(
            f"Unsupported scheme '{parsed.scheme}'. "
            "Please use mysql://user:pass@host:port/database"
        )

    if not parsed.hostname or not parsed.path:
        raise ValueError(
            "DATABASE_URL must contain host, port, and database "
            "(e.g., mysql://user:pass@host:3306/dbname)"
        )

    query = parse_qs(parsed.query)
    config: Dict[str, Any] = {
        "host": parsed.hostname,
        "port": parsed.port or 3306,
        "user": parsed.username,
        "password": parsed.password,
        "database": parsed.path.lstrip("/"),
    }

    # SSL configuration
    if "ssl-ca" in query:
        config["ssl_ca"] = query["ssl-ca"][0]
    if "ssl-cert" in query:
        config["ssl_cert"] = query["ssl-cert"][0]
    if "ssl-key" in query:
        config["ssl_key"] = query["ssl-key"][0]
    if query.get("ssl-verify-cert", ["true"])[0].lower() == "false":
        config["ssl_verify_cert"] = False

    return config


def load_db_config() -> Dict[str, Any]:
    """Load database connection configuration from settings"""
    from xyz_agent_context.settings import settings

    # Prefer DATABASE_URL
    if settings.database_url:
        return parse_database_url(settings.database_url)

    # Otherwise use individual configuration options
    required_values = {
        "DB_HOST": settings.db_host,
        "DB_NAME": settings.db_name,
        "DB_USER": settings.db_user,
        "DB_PASSWORD": settings.db_password,
    }
    missing = [key for key, value in required_values.items() if not value]
    if missing:
        raise RuntimeError(
            "Missing database configuration environment variables: " + ", ".join(missing)
        )

    config: Dict[str, Any] = {
        "host": settings.db_host,
        "port": settings.db_port,
        "user": settings.db_user,
        "password": settings.db_password,
        "database": settings.db_name,
    }

    # SSL configuration (optional)
    if settings.db_ssl_ca:
        config["ssl_ca"] = settings.db_ssl_ca
    if settings.db_ssl_cert:
        config["ssl_cert"] = settings.db_ssl_cert
    if settings.db_ssl_key:
        config["ssl_key"] = settings.db_ssl_key
    if settings.db_ssl_verify_cert is not None:
        config["ssl_verify_cert"] = settings.db_ssl_verify_cert.lower() != "false"

    return config


def validate_identifier(identifier: str) -> str:
    """Validate table/field names for safety (prevent SQL injection)"""
    if not re.fullmatch(r"[A-Za-z0-9_]+", identifier):
        raise ValueError(
            f"Identifier '{identifier}' can only contain letters, digits, and underscores"
        )
    return identifier


def _mysql_to_sqlite_sql(query: str) -> str:
    """
    Translate MySQL-specific SQL syntax to SQLite-compatible syntax.

    Covers DDL (CREATE TABLE) and DML (SELECT/INSERT/UPDATE/DELETE).
    """
    q = query
    # %s -> ?
    q = q.replace("%s", "?")
    # Remove BINARY keyword
    q = re.sub(r'\bBINARY\s+', '', q)
    # Backticks -> double quotes
    q = q.replace('`', '"')
    # CURRENT_TIMESTAMP(6) -> datetime('now')
    q = re.sub(r"CURRENT_TIMESTAMP\(\d+\)", "datetime('now')", q)
    # DATETIME(6) -> TEXT
    q = re.sub(r'\bDATETIME\(\d+\)', 'TEXT', q)
    # BIGINT UNSIGNED NOT NULL AUTO_INCREMENT -> INTEGER PRIMARY KEY AUTOINCREMENT
    q = re.sub(r'\bBIGINT\s+UNSIGNED\s+NOT\s+NULL\s+AUTO_INCREMENT', 'INTEGER', q, flags=re.IGNORECASE)
    # VARCHAR(N) -> TEXT
    q = re.sub(r'\bVARCHAR\(\d+\)', 'TEXT', q)
    # MEDIUMTEXT / LONGTEXT / TINYTEXT -> TEXT
    q = re.sub(r'\b(MEDIUM|LONG|TINY)TEXT\b', 'TEXT', q, flags=re.IGNORECASE)
    # TINYINT(1) -> INTEGER
    q = re.sub(r'\bTINYINT\(\d+\)', 'INTEGER', q, flags=re.IGNORECASE)
    # Remove ON UPDATE CURRENT_TIMESTAMP / ON UPDATE datetime('now')
    q = re.sub(r"\bON\s+UPDATE\s+(CURRENT_TIMESTAMP\(\d+\)|datetime\('now'\))", '', q, flags=re.IGNORECASE)
    # Remove ENGINE=... (to end of statement or next comma)
    q = re.sub(r"\)\s*ENGINE\s*=\s*\w+[^;]*", ')', q, flags=re.IGNORECASE)
    # Remove COMMENT='...' or COMMENT "..."
    q = re.sub(r"\bCOMMENT\s*=?\s*'[^']*'", '', q, flags=re.IGNORECASE)
    q = re.sub(r'\bCOMMENT\s*=?\s*"[^"]*"', '', q, flags=re.IGNORECASE)
    # Remove DEFAULT CHARSET=... COLLATE=...
    q = re.sub(r'\bDEFAULT\s+CHARSET\s*=\s*\w+', '', q, flags=re.IGNORECASE)
    q = re.sub(r'\bCOLLATE\s*=?\s*\w+', '', q, flags=re.IGNORECASE)
    # Remove UNSIGNED
    q = re.sub(r'\bUNSIGNED\b', '', q, flags=re.IGNORECASE)
    # Remove AUTO_INCREMENT (standalone, after we already handled BIGINT combo)
    q = re.sub(r'\bAUTO_INCREMENT\b', '', q, flags=re.IGNORECASE)
    # Clean up extra whitespace
    q = re.sub(r'  +', ' ', q)
    return q


class AsyncDatabaseClient:
    """
    Truly asynchronous database client

    Uses aiomysql for non-blocking I/O, fully compatible with the DatabaseClient interface.

    Key improvements:
    1. Uses aiomysql.Pool instead of synchronous connection pool
    2. All operations are native async, no thread switching required
    3. Supports high concurrency (not limited by thread pool)
    4. Supports lazy initialization: can use DatabaseClient() directly

    Usage examples:
        # Method 1: Direct instantiation (recommended)
        db = AsyncDatabaseClient()
        results = await db.get("users", {"agent_id": "agent_1"})

        # Method 2: Using create() factory method
        db = await AsyncDatabaseClient.create()

        # Method 3: Using context manager
        async with AsyncDatabaseClient() as db:
            results = await db.get("users", {"agent_id": "agent_1"})

        # Interface is identical to the old DatabaseClient
        await db.insert("chat_history", {"message": "Hello"})
        await db.get("chat_history", {"agent_id": "agent_1"})
    """

    def __init__(
        self,
        db_config: Optional[Dict[str, Any]] = None,
        pool_size: int = 10,
        pool_recycle: int = 3600,
        _pool: Optional[aiomysql.Pool] = None,
        _backend: Optional["DatabaseBackend"] = None,
    ):
        """
        Initialize AsyncDatabaseClient

        Supports three methods:
        1. Direct instantiation (lazy init, recommended): db = AsyncDatabaseClient()
        2. Using create() factory method (immediate init): db = await AsyncDatabaseClient.create()
        3. Using create_with_backend() factory method: db = await AsyncDatabaseClient.create_with_backend(backend)

        When _backend is provided, all CRUD and transaction operations are delegated
        to the backend. The aiomysql pool is not used in this mode.

        Args:
            db_config: Database configuration, None to load from environment variables
            pool_size: Connection pool size (default 10)
            pool_recycle: Connection recycle time in seconds (default 3600)
            _pool: Internal use, for passing a pre-created pool from create() method
            _backend: Optional DatabaseBackend instance for delegated mode
        """
        self._db_config = db_config
        self._pool_size = pool_size
        self._pool_recycle = pool_recycle
        self._pool: Optional[aiomysql.Pool] = _pool
        self._transaction_connection: Optional[aiomysql.Connection] = None
        self._initialized = _pool is not None or _backend is not None
        self._backend: Optional["DatabaseBackend"] = _backend
        self._owns_backend: bool = _backend is not None  # Only close if we own it

    async def _ensure_pool(self) -> aiomysql.Pool:
        """
        Ensure the connection pool is initialized (lazy loading)

        If the connection pool is not initialized, create it.
        If DATABASE_URL is sqlite://, uses the shared SQLiteBackend from db_factory.
        Supports calling methods after direct DatabaseClient() instantiation.

        Returns:
            aiomysql connection pool (or None if using SQLite backend)
        """
        if self._backend:
            return None  # Using backend delegation, no pool needed

        if self._pool is None:
            # Check if we should use SQLite instead of MySQL
            from xyz_agent_context.settings import settings
            url = getattr(settings, 'database_url', None) or ''
            if url.startswith('sqlite'):
                # Use the shared singleton from db_factory to avoid multiple connections
                from xyz_agent_context.utils.db_factory import get_db_client
                shared = await get_db_client()
                if shared._backend:
                    self._backend = shared._backend  # Share the same backend
                    self._owns_backend = False  # Don't close it on our close()
                    self._initialized = True
                    return None
                # Fallback: create own backend
                from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend
                from xyz_agent_context.utils.db_factory import parse_sqlite_url
                db_path = parse_sqlite_url(url)
                backend = SQLiteBackend(db_path)
                await backend.initialize()
                self._backend = backend
                self._owns_backend = True
                self._initialized = True
                logger.info(f"AsyncDatabaseClient auto-switched to SQLite backend: {db_path}")
                return None

            if self._db_config is None:
                self._db_config = load_db_config()

            self._pool = await aiomysql.create_pool(
                host=self._db_config['host'],
                port=self._db_config.get('port', 3306),
                user=self._db_config['user'],
                password=self._db_config['password'],
                db=self._db_config['database'],
                minsize=1,
                maxsize=self._pool_size,
                pool_recycle=self._pool_recycle,
                autocommit=True,
                charset='utf8mb4',
            )
            self._initialized = True
            logger.debug(f"AsyncDatabaseClient pool lazily initialized (pool_size={self._pool_size})")

        return self._pool

    @classmethod
    async def create(
        cls,
        db_config: Optional[Dict[str, Any]] = None,
        pool_size: int = 10,
        pool_recycle: int = 3600,
    ) -> 'AsyncDatabaseClient':
        """
        Create an AsyncDatabaseClient instance (factory method, immediate initialization)

        Args:
            db_config: Database configuration, None to load from environment variables
            pool_size: Connection pool size (default 10)
            pool_recycle: Connection recycle time in seconds (default 3600)

        Returns:
            AsyncDatabaseClient instance

        Example:
            db = await AsyncDatabaseClient.create()
            # or
            db = await AsyncDatabaseClient.create(pool_size=20)
        """
        # Check if we should use SQLite backend instead of MySQL
        if db_config is None:
            from xyz_agent_context.settings import settings
            url = getattr(settings, 'database_url', None) or ''
            if url.startswith('sqlite'):
                from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend
                from xyz_agent_context.utils.db_factory import parse_sqlite_url
                db_path = parse_sqlite_url(url)
                backend = SQLiteBackend(db_path)
                await backend.initialize()
                logger.info(f"AsyncDatabaseClient.create() auto-switched to SQLite: {db_path}")
                return cls(_backend=backend)
            db_config = load_db_config()

        # Create aiomysql connection pool
        pool = await aiomysql.create_pool(
            host=db_config['host'],
            port=db_config.get('port', 3306),
            user=db_config['user'],
            password=db_config['password'],
            db=db_config['database'],
            minsize=1,
            maxsize=pool_size,
            pool_recycle=pool_recycle,
            autocommit=True,
            charset='utf8mb4',
        )

        logger.info(f"AsyncDatabaseClient created with pool_size={pool_size}")
        return cls(db_config=db_config, pool_size=pool_size, pool_recycle=pool_recycle, _pool=pool)

    @classmethod
    async def create_with_backend(cls, backend: "DatabaseBackend") -> 'AsyncDatabaseClient':
        """
        Create an AsyncDatabaseClient that delegates all operations to a DatabaseBackend.

        The backend must already be initialized (initialize() called) before passing it here.

        Args:
            backend: An initialized DatabaseBackend instance (e.g., SQLiteBackend, MySQLBackend).

        Returns:
            AsyncDatabaseClient instance in backend-delegated mode.

        Example:
            backend = SQLiteBackend("/path/to/db.sqlite")
            await backend.initialize()
            db = await AsyncDatabaseClient.create_with_backend(backend)
        """
        logger.info(f"AsyncDatabaseClient created with {backend.dialect} backend")
        return cls(_backend=backend)

    # ===== Basic CRUD Operations =====

    async def execute(
        self,
        query: str,
        params: Optional[tuple] = None,
        fetch: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Execute SQL query (truly async, no thread switching)

        Args:
            query: SQL query statement
            params: Query parameters
            fetch: Whether to return results

        Returns:
            List of query results
        """
        if self._backend:
            # Auto-translate MySQL SQL dialect to backend dialect
            q = query
            p = params
            if self._backend.dialect == "sqlite":
                q = _mysql_to_sqlite_sql(q)
                p = tuple(p) if p else ()
            if fetch:
                return await self._backend.execute(q, p)
            else:
                return await self._backend.execute_write(q, p)

        await self._ensure_pool()
        if self._backend:
            # _ensure_pool auto-switched to SQLite — delegate with translation
            q = _mysql_to_sqlite_sql(query) if self._backend.dialect == "sqlite" else query
            p = tuple(params) if params else ()
            return (await self._backend.execute(q, p)) if fetch else (await self._backend.execute_write(q, p))
        pool = self._pool

        if self._transaction_connection:
            # Use transaction connection
            async with self._transaction_connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(query, params or ())
                if fetch:
                    return await cursor.fetchall()
                return cursor.rowcount  # Return affected row count
        else:
            # Acquire connection from pool
            async with pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cursor:
                    await cursor.execute(query, params or ())
                    if fetch:
                        return await cursor.fetchall()
                    return cursor.rowcount  # Return affected row count

    async def get(
        self,
        table: str,
        filters: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order_by: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Query data

        Args:
            table: Table name
            filters: Filter conditions
            limit: Result limit
            offset: Result offset
            order_by: Sort order

        Returns:
            List of query results
        """
        if self._backend:
            return await self._backend.get(table, filters, limit, offset, order_by)

        safe_table = validate_identifier(table)
        query = f"SELECT * FROM `{safe_table}`"
        params = []

        if filters:
            where_clauses = []
            for key, value in filters.items():
                safe_key = validate_identifier(key)
                where_clauses.append(f"`{safe_key}` = %s")
                params.append(value)
            query += " WHERE " + " AND ".join(where_clauses)

        if order_by:
            order_parts = order_by.split()
            safe_order_field = validate_identifier(order_parts[0])
            direction = ""
            if len(order_parts) > 1 and order_parts[1].upper() in ("ASC", "DESC"):
                direction = " " + order_parts[1].upper()
            query += f" ORDER BY `{safe_order_field}`{direction}"

        if limit is not None:
            query += f" LIMIT {int(limit)}"
        if offset is not None:
            query += f" OFFSET {int(offset)}"

        return await self.execute(query, tuple(params), fetch=True)

    async def get_one(
        self,
        table: str,
        filters: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Query a single record"""
        if self._backend:
            return await self._backend.get_one(table, filters)

        logger.debug(f"              → DB.get_one('{table}', filters={filters})")
        results = await self.get(table, filters, limit=1)
        logger.debug(f"              ← DB.get_one: {'Found' if results else 'Not found'}")
        return results[0] if results else None

    async def get_by_ids(
        self,
        table: str,
        id_field: str,
        ids: List[str]
    ) -> List[Dict[str, Any]]:
        """
        Batch query (core method for solving the N+1 problem)

        Uses a single IN query to replace multiple individual queries.

        Args:
            table: Table name
            id_field: ID field name
            ids: List of IDs

        Returns:
            List of query results (in original ID order)

        Example:
            # Before (N+1):
            for event_id in event_ids:
                event = await db.get_one("events", {"event_id": event_id})

            # After (batch):
            events = await db.get_by_ids("events", "event_id", event_ids)
        """
        if self._backend:
            return await self._backend.get_by_ids(table, id_field, ids)

        if not ids:
            return []

        # Deduplicate while preserving order
        unique_ids = list(dict.fromkeys(ids))

        safe_table = validate_identifier(table)
        safe_id_field = validate_identifier(id_field)

        # Build IN query
        placeholders = ','.join(['%s'] * len(unique_ids))
        query = f"SELECT * FROM `{safe_table}` WHERE `{safe_id_field}` IN ({placeholders})"

        results = await self.execute(query, tuple(unique_ids), fetch=True)

        # Create lookup map
        result_map = {row[id_field]: row for row in results}

        # Return in original order
        return [result_map.get(id) for id in ids]

    async def insert(
        self,
        table: str,
        data: Dict[str, Any]
    ) -> int:
        """
        Insert data

        Args:
            table: Table name
            data: Data to insert

        Returns:
            Auto-increment ID of the inserted row
        """
        if self._backend:
            # Filter out None values (same as MySQL path below)
            filtered = {k: v for k, v in data.items() if v is not None}
            if not filtered:
                raise ValueError("Insert data cannot be empty (no valid fields after filtering None values)")
            return await self._backend.insert(table, filtered)

        if not data:
            raise ValueError("Insert data cannot be empty")

        # Filter out None values to let MySQL DEFAULT take effect
        # (Explicitly passing NULL would override column DEFAULT values, causing errors on NOT NULL columns)
        data = {k: v for k, v in data.items() if v is not None}

        if not data:
            raise ValueError("Insert data cannot be empty (no valid fields after filtering None values)")

        logger.debug(f"              → DB.insert('{table}', {len(data)} fields)")

        safe_table = validate_identifier(table)
        safe_keys = [validate_identifier(key) for key in data.keys()]

        columns = ", ".join(f"`{key}`" for key in safe_keys)
        placeholders = ", ".join(["%s"] * len(data))
        query = f"INSERT INTO `{safe_table}` ({columns}) VALUES ({placeholders})"
        params = tuple(data.values())

        await self._ensure_pool()
        if self._backend:
            # _ensure_pool auto-switched to SQLite — delegate with translation
            q = _mysql_to_sqlite_sql(query) if self._backend.dialect == "sqlite" else query
            p = tuple(params) if params else ()
            return (await self._backend.execute(q, p)) if fetch else (await self._backend.execute_write(q, p))
        pool = self._pool

        if self._transaction_connection:
            async with self._transaction_connection.cursor() as cursor:
                await cursor.execute(query, params)
                lastrowid = cursor.lastrowid
        else:
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(query, params)
                    lastrowid = cursor.lastrowid

        logger.debug(f"              ← DB.insert: lastrowid={lastrowid}")
        return lastrowid

    async def update(
        self,
        table: str,
        filters: Dict[str, Any],
        data: Dict[str, Any]
    ) -> int:
        """
        Update data

        Args:
            table: Table name
            filters: Filter conditions
            data: Data to update

        Returns:
            Number of rows updated
        """
        if self._backend:
            return await self._backend.update(table, filters, data)

        if not data:
            raise ValueError("Update data cannot be empty")
        if not filters:
            raise ValueError("Update operation must specify filter conditions")

        logger.debug(f"              → DB.update('{table}', filters={filters}, {len(data)} fields)")

        safe_table = validate_identifier(table)

        set_clauses = []
        params = []
        for key, value in data.items():
            safe_key = validate_identifier(key)
            set_clauses.append(f"`{safe_key}` = %s")
            params.append(value)

        where_clauses = []
        for key, value in filters.items():
            safe_key = validate_identifier(key)
            where_clauses.append(f"`{safe_key}` = %s")
            params.append(value)

        query = (
            f"UPDATE `{safe_table}` "
            f"SET {', '.join(set_clauses)} "
            f"WHERE {' AND '.join(where_clauses)}"
        )

        await self._ensure_pool()
        if self._backend:
            # _ensure_pool auto-switched to SQLite — delegate with translation
            q = _mysql_to_sqlite_sql(query) if self._backend.dialect == "sqlite" else query
            p = tuple(params) if params else ()
            return (await self._backend.execute(q, p)) if fetch else (await self._backend.execute_write(q, p))
        pool = self._pool

        if self._transaction_connection:
            async with self._transaction_connection.cursor() as cursor:
                await cursor.execute(query, tuple(params))
                rowcount = cursor.rowcount
        else:
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(query, tuple(params))
                    rowcount = cursor.rowcount

        logger.debug(f"              ← DB.update: {rowcount} rows affected")
        return rowcount

    async def delete(
        self,
        table: str,
        filters: Dict[str, Any]
    ) -> int:
        """
        Delete data

        Args:
            table: Table name
            filters: Filter conditions

        Returns:
            Number of rows deleted
        """
        if self._backend:
            return await self._backend.delete(table, filters)

        if not filters:
            raise ValueError("Delete operation must specify filter conditions")

        safe_table = validate_identifier(table)

        where_clauses = []
        params = []
        for key, value in filters.items():
            safe_key = validate_identifier(key)
            where_clauses.append(f"`{safe_key}` = %s")
            params.append(value)

        query = f"DELETE FROM `{safe_table}` WHERE {' AND '.join(where_clauses)}"

        await self._ensure_pool()
        if self._backend:
            # _ensure_pool auto-switched to SQLite — delegate with translation
            q = _mysql_to_sqlite_sql(query) if self._backend.dialect == "sqlite" else query
            p = tuple(params) if params else ()
            return (await self._backend.execute(q, p)) if fetch else (await self._backend.execute_write(q, p))
        pool = self._pool

        if self._transaction_connection:
            async with self._transaction_connection.cursor() as cursor:
                await cursor.execute(query, tuple(params))
                rowcount = cursor.rowcount
        else:
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(query, tuple(params))
                    rowcount = cursor.rowcount

        return rowcount

    async def upsert(
        self,
        table: str,
        data: Dict[str, Any],
        id_field: str
    ) -> int:
        """
        Concurrency-safe insert or update (using INSERT ... ON DUPLICATE KEY UPDATE)

        Unlike the query-then-insert/update approach, this method uses database-level
        atomic operations, ensuring no race conditions under high concurrency.

        Args:
            table: Table name
            data: Data to insert/update
            id_field: Primary key field name (used to determine insert or update)

        Returns:
            Number of affected rows (1=new insert, 2=updated existing record)

        Example:
            # Insert if narrative_id doesn't exist, update if it does
            affected = await db.upsert(
                "narratives",
                {"narrative_id": "nar_123", "title": "New Title", ...},
                "narrative_id"
            )
        """
        if self._backend:
            return await self._backend.upsert(table, data, id_field)

        if not data:
            raise ValueError("Insert data cannot be empty")

        logger.debug(f"              → DB.upsert('{table}', {len(data)} fields)")

        safe_table = validate_identifier(table)
        safe_keys = [validate_identifier(key) for key in data.keys()]
        safe_id_field = validate_identifier(id_field)

        # Build INSERT part
        columns = ", ".join(f"`{key}`" for key in safe_keys)
        placeholders = ", ".join(["%s"] * len(data))

        # Build ON DUPLICATE KEY UPDATE part (excluding primary key)
        # Uses MySQL 8.0.20+ recommended new syntax: INSERT INTO ... AS new_row ... ON DUPLICATE KEY UPDATE col = new_row.col
        update_clauses = []
        for key in safe_keys:
            if key != safe_id_field:
                update_clauses.append(f"`{key}` = new_row.`{key}`")

        query = f"INSERT INTO `{safe_table}` ({columns}) VALUES ({placeholders}) AS new_row"
        if update_clauses:
            query += f" ON DUPLICATE KEY UPDATE {', '.join(update_clauses)}"

        params = tuple(data.values())

        await self._ensure_pool()
        if self._backend:
            # _ensure_pool auto-switched to SQLite — delegate with translation
            q = _mysql_to_sqlite_sql(query) if self._backend.dialect == "sqlite" else query
            p = tuple(params) if params else ()
            return (await self._backend.execute(q, p)) if fetch else (await self._backend.execute_write(q, p))
        pool = self._pool

        if self._transaction_connection:
            async with self._transaction_connection.cursor() as cursor:
                await cursor.execute(query, params)
                rowcount = cursor.rowcount
        else:
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(query, params)
                    rowcount = cursor.rowcount

        logger.debug(f"              ← DB.upsert: {rowcount} rows affected")
        return rowcount

    # ===== Pydantic Model Support =====

    async def insert_model(self, table: str, model: BaseModel) -> int:
        """Insert a Pydantic model"""
        data = self._serialize_model(model)
        return await self.insert(table, data)

    async def update_model(
        self,
        table: str,
        filters: Dict[str, Any],
        model: BaseModel
    ) -> int:
        """Update a Pydantic model"""
        data = self._serialize_model(model)
        return await self.update(table, filters, data)

    async def query_models(
        self,
        table: str,
        model_class: Type[T],
        filters: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order_by: Optional[str] = None
    ) -> List[T]:
        """Query and return a list of Pydantic models"""
        results = await self.get(table, filters, limit, offset, order_by)
        return [self._deserialize_model(model_class, row) for row in results]

    async def get_model(
        self,
        table: str,
        model_class: Type[T],
        filters: Dict[str, Any]
    ) -> Optional[T]:
        """Query a single Pydantic model"""
        models = await self.query_models(table, model_class, filters, limit=1)
        return models[0] if models else None

    def _serialize_model(self, model: BaseModel) -> Dict[str, Any]:
        """Serialize a Pydantic model to a dictionary"""
        data = model.model_dump()
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = value.isoformat()
            elif isinstance(value, (list, dict)) and value:
                data[key] = json.dumps(value, ensure_ascii=False)
        return data

    def _deserialize_model(self, model_class: Type[T], data: Dict[str, Any]) -> T:
        """Deserialize a dictionary to a Pydantic model"""
        processed_data = {}
        for key, value in data.items():
            if isinstance(value, str):
                if value.startswith('[') or value.startswith('{'):
                    try:
                        processed_data[key] = json.loads(value)
                    except json.JSONDecodeError:
                        processed_data[key] = value
                else:
                    processed_data[key] = value
            else:
                processed_data[key] = value
        return model_class(**processed_data)

    # ===== Transaction Support =====

    async def begin_transaction(self) -> None:
        """Begin a transaction"""
        if self._backend:
            return await self._backend.begin_transaction()

        if self._transaction_connection:
            raise RuntimeError("Already in a transaction")

        pool = await self._ensure_pool()
        self._transaction_connection = await pool.acquire()
        await self._transaction_connection.begin()

    async def commit(self) -> None:
        """Commit the transaction"""
        if self._backend:
            return await self._backend.commit()

        if not self._transaction_connection:
            raise RuntimeError("No active transaction")

        pool = await self._ensure_pool()
        await self._transaction_connection.commit()
        pool.release(self._transaction_connection)
        self._transaction_connection = None

    async def rollback(self) -> None:
        """Rollback the transaction"""
        if self._backend:
            return await self._backend.rollback()

        if not self._transaction_connection:
            raise RuntimeError("No active transaction")

        pool = await self._ensure_pool()
        await self._transaction_connection.rollback()
        pool.release(self._transaction_connection)
        self._transaction_connection = None

    @asynccontextmanager
    async def transaction(self):
        """Transaction context manager"""
        await self.begin_transaction()
        try:
            yield
            await self.commit()
        except Exception:
            await self.rollback()
            raise

    # ===== Table Management =====

    async def create_table(self, table_schema: str) -> None:
        """Create a table"""
        await self.execute(table_schema, fetch=False)

    async def table_exists(self, table_name: str) -> bool:
        """Check if a table exists"""
        query = """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = DATABASE() AND table_name = %s
        """
        results = await self.execute(query, (table_name,), fetch=True)
        return results[0]['COUNT(*)'] > 0 if results else False

    async def drop_table(self, table_name: str) -> None:
        """Drop a table"""
        safe_table = validate_identifier(table_name)
        query = f"DROP TABLE IF EXISTS `{safe_table}`"
        await self.execute(query, fetch=False)

    # ===== Semantic Search =====

    async def semantic_search(
        self,
        table: str,
        embedding_column: str,
        query_embedding: List[float],
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 10,
        min_similarity: float = 0.0,
    ) -> List[tuple[Dict[str, Any], float]]:
        """
        Semantic similarity search

        Uses numpy to compute cosine similarity, returns results sorted by similarity in descending order.

        Args:
            table: Table name
            embedding_column: Embedding column name (stores JSON-format vectors)
            query_embedding: Query vector
            filters: Additional filter conditions (e.g., {"agent_id": "xxx"})
            limit: Result count limit
            min_similarity: Minimum similarity threshold (0.0 - 1.0)

        Returns:
            List of (row_dict, similarity_score) tuples, sorted by similarity in descending order

        Example:
            results = await db.semantic_search(
                table="job_table",
                embedding_column="embedding",
                query_embedding=[0.1, 0.2, ...],
                filters={"agent_id": "agent_123"},
                limit=5,
                min_similarity=0.5
            )
            for row, score in results:
                print(f"{row['title']}: {score:.4f}")
        """
        import numpy as np

        # Validate table name and column name
        safe_table = validate_identifier(table)
        safe_column = validate_identifier(embedding_column)

        # Build query
        query = f"SELECT * FROM `{safe_table}` WHERE `{safe_column}` IS NOT NULL"
        params = []

        if filters:
            for key, value in filters.items():
                safe_key = validate_identifier(key)
                query += f" AND `{safe_key}` = %s"
                params.append(value)

        results = await self.execute(query, tuple(params), fetch=True)

        if not results:
            return []

        # Convert query vector
        query_vec = np.array(query_embedding)
        query_norm = np.linalg.norm(query_vec)

        if query_norm == 0:
            return []

        # Calculate similarity
        scored_results: List[tuple[Dict[str, Any], float]] = []

        for row in results:
            embedding = row.get(embedding_column)
            if embedding is None:
                continue

            # Parse embedding (may be a JSON string)
            if isinstance(embedding, str):
                embedding = json.loads(embedding)

            if not embedding:
                continue

            # Calculate cosine similarity
            row_vec = np.array(embedding)
            row_norm = np.linalg.norm(row_vec)

            if row_norm == 0:
                continue

            similarity = float(np.dot(query_vec, row_vec) / (query_norm * row_norm))

            if similarity >= min_similarity:
                scored_results.append((row, similarity))

        # Sort by similarity in descending order
        scored_results.sort(key=lambda x: x[1], reverse=True)

        return scored_results[:limit]

    # ===== Connection Management =====

    async def ping(self) -> bool:
        """Check if the connection is healthy"""
        try:
            pool = await self._ensure_pool()
            async with pool.acquire() as conn:
                await conn.ping()
            return True
        except Exception:
            return False

    async def close(self) -> None:
        """Close the connection pool or backend"""
        if self._backend:
            if self._owns_backend:
                await self._backend.close()
                logger.info("AsyncDatabaseClient (backend-delegated) closed")
            else:
                logger.debug("AsyncDatabaseClient detached from shared backend (not closing)")
            self._backend = None
            return

        if self._pool is None:
            # Connection pool not initialized, no need to close
            return

        if self._transaction_connection:
            self._pool.release(self._transaction_connection)
            self._transaction_connection = None

        self._pool.close()
        await self._pool.wait_closed()
        self._pool = None
        logger.info("AsyncDatabaseClient closed")

    async def __aenter__(self) -> 'AsyncDatabaseClient':
        # Ensure the connection pool is initialized before use
        await self._ensure_pool()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()
