"""
@file_name: database.py
@author: NetMind.AI
@date: 2025-11-28
@description: Truly asynchronous database client with pluggable backend support

This is the project's main database client. Every operation is delegated to a
`DatabaseBackend` — SQLiteBackend for local/desktop, MySQLBackend for cloud,
SQLiteProxyBackend when a proxy is configured. The backend is either supplied
via create_with_backend() or resolved lazily from DATABASE_URL.

It used to have a second mode that drove an aiomysql pool directly. That path
was reachable only by passing `_pool=` to the constructor, which nothing ever
did; being unreachable, it drifted, and the 2026-08-17 transaction fix had to be
written twice with the copy silently missing four of its twin's guards. Removed
2026-08-18 per binding rule #2 (no backwards-compat shims); the mirror md
carries the full rationale. There is one path now.

Usage examples:
    # Lazy, resolved from DATABASE_URL
    db = await AsyncDatabaseClient.create()

    # Backend-delegated mode (SQLite)
    from narranexus.platform.utils.db.db_backend_sqlite import SQLiteBackend
    backend = SQLiteBackend("/path/to/db.sqlite")
    await backend.initialize()
    db = await AsyncDatabaseClient.create_with_backend(backend)

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

from loguru import logger
from pydantic import BaseModel

if TYPE_CHECKING:
    from narranexus.platform.utils.db.db_backend import DatabaseBackend

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
    from narranexus.platform.settings import settings

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


def _get_unique_cols_for_table(table_name: str) -> list[str]:
    """
    Look up the unique index columns for a table from schema_registry.
    Falls back to the first column if no unique index is found.
    """
    try:
        from narranexus.platform.utils.db.schema_registry import TABLES
        table_def = TABLES.get(table_name)
        if table_def:
            for idx in table_def.indexes:
                if idx.unique:
                    return idx.columns
            # No unique index found — use primary key columns
            pk_cols = [c.name for c in table_def.columns if c.primary_key]
            if pk_cols:
                return pk_cols
    except ImportError:
        pass
    # Fallback: first column
    return [table_name]


def _mysql_to_sqlite_sql(query: str) -> str:
    """
    Translate MySQL-specific SQL syntax to SQLite-compatible syntax.

    Covers DDL (CREATE TABLE) and DML (SELECT/INSERT/UPDATE/DELETE).
    """
    q = query

    # information_schema.tables → SQLite sqlite_master
    # Handles: SELECT COUNT(*) [as cnt] FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = %s
    if re.search(r'information_schema\.tables', q, flags=re.IGNORECASE):
        q = "SELECT COUNT(*) as cnt FROM sqlite_master WHERE type='table' AND name=?"
        return q

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
    # ── MySQL UPSERT (Pattern A): INSERT ... AS alias ON DUPLICATE KEY UPDATE alias.col ──
    # MySQL 8.0.20+ syntax → SQLite ON CONFLICT DO UPDATE SET col = excluded.col
    mysql_upsert_alias = re.search(
        r'INSERT\s+INTO\s+"?(\w+)"?\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)\s*AS\s+(\w+)\s+'
        r'ON\s+DUPLICATE\s+KEY\s+UPDATE\s+(.*)',
        q, flags=re.IGNORECASE | re.DOTALL
    )
    if mysql_upsert_alias:
        table = mysql_upsert_alias.group(1)
        cols = mysql_upsert_alias.group(2)
        vals = mysql_upsert_alias.group(3)
        alias = mysql_upsert_alias.group(4)
        update_clause = mysql_upsert_alias.group(5).strip().rstrip(';')
        update_clause = re.sub(
            rf'{alias}\."?(\w+)"?',
            r'excluded."\1"',
            update_clause
        )
        conflict_cols = _get_unique_cols_for_table(table)
        conflict_target = ", ".join(f'"{c}"' for c in conflict_cols)
        q = f'INSERT INTO "{table}" ({cols}) VALUES ({vals}) ON CONFLICT({conflict_target}) DO UPDATE SET {update_clause}'

    # ── MySQL UPSERT (Pattern B): INSERT ... ON DUPLICATE KEY UPDATE col = VALUES(col) ──
    # Legacy MySQL syntax → SQLite ON CONFLICT DO UPDATE SET col = excluded.col
    elif re.search(r'ON\s+DUPLICATE\s+KEY\s+UPDATE', q, flags=re.IGNORECASE):
        mysql_upsert_values = re.search(
            r'(INSERT\s+INTO\s+"?(\w+)"?\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\))\s+'
            r'ON\s+DUPLICATE\s+KEY\s+UPDATE\s+(.*)',
            q, flags=re.IGNORECASE | re.DOTALL
        )
        if mysql_upsert_values:
            insert_part = mysql_upsert_values.group(1)
            table = mysql_upsert_values.group(2)
            cols = mysql_upsert_values.group(3)
            update_clause = mysql_upsert_values.group(5).strip().rstrip(';')
            # VALUES(col) -> excluded."col"
            update_clause = re.sub(
                r'VALUES\("?(\w+)"?\)',
                r'excluded."\1"',
                update_clause, flags=re.IGNORECASE
            )
            # Find the correct unique key from schema_registry
            conflict_cols = _get_unique_cols_for_table(table)
            conflict_target = ", ".join(f'"{c}"' for c in conflict_cols)
            q = f'{insert_part} ON CONFLICT({conflict_target}) DO UPDATE SET {update_clause}'

    # INSERT IGNORE -> INSERT OR IGNORE
    q = re.sub(r'\bINSERT\s+IGNORE\b', 'INSERT OR IGNORE', q, flags=re.IGNORECASE)

    # JSON_ARRAY_APPEND(col, '$', val) -> json_insert(col, '$[#]', val)
    # SQLite json_insert with '$[#]' appends to end of array
    q = re.sub(
        r"JSON_ARRAY_APPEND\s*\(\s*(\w+)\s*,\s*'\$'\s*,",
        r"json_insert(\1, '$[#]',",
        q, flags=re.IGNORECASE
    )

    # Remove FOR UPDATE / FOR UPDATE SKIP LOCKED (MySQL row locking)
    q = re.sub(r'\bFOR\s+UPDATE(\s+SKIP\s+LOCKED)?\b', '', q, flags=re.IGNORECASE)

    # NOW() -> datetime('now')
    q = re.sub(r'\bNOW\(\)', "datetime('now')", q, flags=re.IGNORECASE)
    # DATE_SUB(datetime('now'), INTERVAL ? DAY) -> datetime('now', '-' || ? || ' days')
    q = re.sub(
        r"DATE_SUB\s*\(\s*datetime\('now'\)\s*,\s*INTERVAL\s+\?\s+DAY\s*\)",
        "datetime('now', '-' || ? || ' days')",
        q, flags=re.IGNORECASE
    )

    # JSON_UNQUOTE(JSON_EXTRACT(col, path)) -> json_extract(col, path)
    # SQLite's json_extract already returns unquoted strings
    q = re.sub(
        r'JSON_UNQUOTE\s*\(\s*JSON_EXTRACT\s*\(([^)]+),\s*([^)]+)\)\s*\)',
        r'json_extract(\1, \2)',
        q, flags=re.IGNORECASE
    )

    # JSON_SEARCH(col, 'one', val) IS NOT NULL -> EXISTS(SELECT 1 FROM json_each(col) WHERE value = val)
    q = re.sub(
        r"JSON_SEARCH\s*\(\s*(\w+)\s*,\s*'one'\s*,\s*(\?)\s*\)\s*IS\s+NOT\s+NULL",
        r'EXISTS(SELECT 1 FROM json_each(\1) WHERE value = \2)',
        q, flags=re.IGNORECASE
    )

    # JSON_CONTAINS(JSON_EXTRACT(col, path), JSON_OBJECT('id', ?, 'type', 'participant'))
    # -> EXISTS(SELECT 1 FROM json_each(json_extract(col, path)) WHERE json_extract(value, '$.id') = ? AND json_extract(value, '$.type') = 'participant')
    json_contains_match = re.search(
        r"JSON_CONTAINS\s*\(\s*JSON_EXTRACT\s*\(\s*(\w+)\s*,\s*'([^']+)'\s*\)\s*,\s*"
        r"JSON_OBJECT\s*\(\s*'(\w+)'\s*,\s*(\?)\s*,\s*'(\w+)'\s*,\s*'(\w+)'\s*\)\s*\)",
        q, flags=re.IGNORECASE
    )
    if json_contains_match:
        col = json_contains_match.group(1)
        path = json_contains_match.group(2)
        key1 = json_contains_match.group(3)
        val1 = json_contains_match.group(4)
        key2 = json_contains_match.group(5)
        val2 = json_contains_match.group(6)
        replacement = (
            f"EXISTS(SELECT 1 FROM json_each(json_extract({col}, '{path}')) "
            f"WHERE json_extract(value, '$.{key1}') = {val1} "
            f"AND json_extract(value, '$.{key2}') = '{val2}')"
        )
        q = q[:json_contains_match.start()] + replacement + q[json_contains_match.end():]

    # JSON_CONTAINS(col, ?) -> EXISTS(SELECT 1 FROM json_each(col) WHERE value = json_extract(?, '$'))
    # Used by SocialNetworkRepository.search_by_name_or_alias for the
    # `JSON_CONTAINS(aliases, json.dumps(name))` form (a single JSON-quoted
    # scalar). MySQL natively understands this; SQLite's json1 extension does
    # not have JSON_CONTAINS at all, so we expand to a json_each scan. The
    # `json_extract(?, '$')` strips the outer JSON quotes from the bound
    # param so the comparison is against the unquoted scalar that json_each
    # exposes via `value`.
    q = re.sub(
        r"JSON_CONTAINS\s*\(\s*(\w+)\s*,\s*\?\s*\)",
        r"EXISTS(SELECT 1 FROM json_each(\1) WHERE value = json_extract(?, '$'))",
        q, flags=re.IGNORECASE
    )

    # Clean up extra whitespace
    q = re.sub(r'  +', ' ', q)
    return q


async def _backend_from_settings(
    db_config: Optional[Dict[str, Any]] = None,
    pool_size: int = 10,
    pool_recycle: int = 3600,
) -> "DatabaseBackend":
    """Build (and initialise) the backend this deployment's settings call for.

    One ladder, used by both `AsyncDatabaseClient.create()` and
    `_ensure_backend()`. They used to carry a copy each; the only real
    difference between them is whether the caller first tries to share
    `db_factory`'s singleton, and that difference now lives at the call site
    instead of being buried in two near-identical blocks.
    """
    if db_config is None:
        from narranexus.platform.settings import settings
        url = getattr(settings, 'database_url', None) or ''
        if url.startswith('sqlite'):
            import os
            proxy_url = os.environ.get("SQLITE_PROXY_URL", "")
            if proxy_url:
                from narranexus.platform.utils.db.db_backend_sqlite_proxy import SQLiteProxyBackend
                backend = SQLiteProxyBackend(proxy_url)
                await backend.initialize()
                logger.info(f"AsyncDatabaseClient resolved SQLite Proxy backend: {proxy_url}")
                return backend
            from narranexus.platform.utils.db.db_backend_sqlite import SQLiteBackend
            from narranexus.platform.utils.db.db_factory import parse_sqlite_url
            db_path = parse_sqlite_url(url)
            backend = SQLiteBackend(db_path)
            await backend.initialize()
            logger.info(f"AsyncDatabaseClient resolved SQLite backend: {db_path}")
            return backend
        db_config = load_db_config()

    from narranexus.platform.utils.db.db_backend_mysql import MySQLBackend
    backend = MySQLBackend(db_config, pool_size=pool_size, pool_recycle=pool_recycle)
    await backend.initialize()
    logger.info(f"AsyncDatabaseClient resolved MySQL backend (pool_size={pool_size})")
    return backend


class AsyncDatabaseClient:
    """
    Truly asynchronous database client

    A dialect-agnostic facade: every call is delegated to a `DatabaseBackend`,
    which owns the driver and its connection handling.

    Key properties:
    1. All operations are native async, no thread switching required
    2. Supports high concurrency (bounded by the backend's own pool, not threads)
    3. Supports lazy initialization: can use AsyncDatabaseClient() directly

    Usage examples:
        # Method 1: Direct instantiation (recommended)
        db = AsyncDatabaseClient()
        results = await db.get("users", {"agent_id": "agent_1"})

        # Method 2: Using create() factory method
        db = await AsyncDatabaseClient.create()

        # Method 3: Using context manager
        async with AsyncDatabaseClient() as db:
            results = await db.get("users", {"agent_id": "agent_1"})

        await db.insert("chat_history", {"message": "Hello"})
        await db.get("chat_history", {"agent_id": "agent_1"})
    """

    def __init__(
        self,
        db_config: Optional[Dict[str, Any]] = None,
        pool_size: int = 10,
        pool_recycle: int = 3600,
        _backend: Optional["DatabaseBackend"] = None,
    ):
        """
        Initialize AsyncDatabaseClient

        Supports three methods:
        1. Direct instantiation (lazy init, recommended): db = AsyncDatabaseClient()
        2. Using create() factory method (immediate init): db = await AsyncDatabaseClient.create()
        3. Using create_with_backend() factory method: db = await AsyncDatabaseClient.create_with_backend(backend)

        When _backend is provided, all CRUD and transaction operations are delegated
        to the backend.

        Args:
            db_config: Database configuration, None to load from environment variables
            pool_size: Connection pool size (default 10)
            pool_recycle: Connection recycle time in seconds (default 3600)
            _backend: Optional DatabaseBackend instance for delegated mode
        """
        self._db_config = db_config
        self._pool_size = pool_size
        self._pool_recycle = pool_recycle

        self._initialized = _backend is not None
        self._backend: Optional["DatabaseBackend"] = _backend
        self._owns_backend: bool = _backend is not None  # Only close if we own it

    async def _ensure_backend(self) -> "DatabaseBackend":
        """Ensure the backend is initialized (lazy), and return it.

        Every path here ends in a `DatabaseBackend`. This client used to carry a
        second, parallel implementation that talked to an `aiomysql` pool
        directly — reachable only by passing `_pool=` to `__init__`, which
        nothing ever did. It was dead on arrival and, being dead, drifted: the
        transaction fix of 2026-08-17 had to be written twice, and the copy here
        silently missed four of the guards its twin got. Deleted per binding
        rule #2 — the file header already declared single-backend delegation as
        the design; now the code says so too.
        """
        if self._backend:
            return self._backend

        # Unlike `create()`, a lazily-resolved client prefers to SHARE
        # db_factory's singleton when the deployment is on SQLite: one process
        # must not open several connections to the same file. `_owns_backend`
        # stays False so our `close()` leaves the shared backend alone.
        from narranexus.platform.settings import settings
        url = getattr(settings, 'database_url', None) or ''
        if url.startswith('sqlite'):
            from narranexus.platform.utils.db.db_factory import get_db_client
            shared = await get_db_client()
            if shared._backend:
                self._backend = shared._backend
                self._owns_backend = False
                self._initialized = True
                return self._backend

        self._backend = await _backend_from_settings(
            self._db_config, self._pool_size, self._pool_recycle
        )
        self._owns_backend = True
        self._initialized = True
        return self._backend

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
        # Always its own backend: `create()` is the "give me a client I own"
        # entry point, so unlike `_ensure_backend()` it deliberately does NOT
        # borrow db_factory's shared singleton — `close()` on this client must
        # be free to tear its backend down.
        backend = await _backend_from_settings(db_config, pool_size, pool_recycle)
        return cls(_backend=backend)

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
        backend = await self._ensure_backend()

        # Auto-translate MySQL SQL dialect to the backend's dialect. One copy:
        # this used to be duplicated for the already-initialised and the
        # lazily-initialised case, and the two had already drifted on how they
        # normalised `params`.
        q = query
        p = params
        if backend.dialect == "sqlite":
            q = _mysql_to_sqlite_sql(q)
            p = tuple(p) if p else ()

        if fetch:
            return await backend.execute(q, p)
        return await backend.execute_write(q, p)

    async def get(
        self,
        table: str,
        filters: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order_by: Optional[str] = None,
        fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Query data

        Args:
            table: Table name
            filters: Filter conditions
            limit: Maximum rows to return
            offset: Rows to skip
            order_by: Column to order by, optionally with ASC/DESC
            fields: Column projection; None selects every column

        Returns:
            List of query results
        """
        backend = await self._ensure_backend()
        return await backend.get(
            table, filters, limit, offset, order_by, fields
        )

    async def get_one(
        self,
        table: str,
        filters: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Query a single record"""
        backend = await self._ensure_backend()
        return await backend.get_one(table, filters)

    async def get_by_ids(
        self,
        table: str,
        id_field: str,
        ids: List[Any],
        fields: Optional[List[str]] = None,
    ) -> List[Optional[Dict[str, Any]]]:
        """Batch-fetch rows by id, preserving the order of `ids`.

        Missing ids come back as `None` placeholders, so the result lines up
        positionally with the input — that contract lives in the backends (see
        the mirror md), which is why this only delegates.

        Example:
            # Before (N+1):
            events = [await db.get_one("events", {"event_id": i}) for i in ids]
            # After (batch):
            events = await db.get_by_ids("events", "event_id", event_ids)
        """
        backend = await self._ensure_backend()
        return await backend.get_by_ids(table, id_field, ids, fields=fields)

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
        # Validated BEFORE resolving a backend: these are caller mistakes, and
        # paying for a connection first would both waste it and, when the
        # database is unreachable, bury the real cause under a connection error.
        if not data:
            raise ValueError("Insert data cannot be empty")

        # Drop None values so column DEFAULTs apply. Passing NULL explicitly
        # would override them and fail on NOT NULL columns. This is the one bit
        # of business semantics the facade keeps — the backends do not filter.
        filtered = {k: v for k, v in data.items() if v is not None}
        if not filtered:
            raise ValueError("Insert data cannot be empty (no valid fields after filtering None values)")

        backend = await self._ensure_backend()
        return await backend.insert(table, filtered)

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
            Number of affected rows
        """
        if not data:
            raise ValueError("Update data cannot be empty")
        if not filters:
            raise ValueError("Update operation must specify filter conditions")

        backend = await self._ensure_backend()
        return await backend.update(table, filters, data)

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
            Number of affected rows
        """
        if not filters:
            raise ValueError("Delete operation must specify filter conditions")

        backend = await self._ensure_backend()
        return await backend.delete(table, filters)

    async def upsert(
        self,
        table: str,
        data: Dict[str, Any],
        id_field: str
    ) -> int:
        """
        Insert or update on duplicate key.

        SQL generation belongs to the backend: MySQL emits
        `INSERT ... AS new_row ON DUPLICATE KEY UPDATE` (8.0.20+) while SQLite
        emits its own upsert form. Building the statement here would mean
        handing MySQL syntax to whichever backend happened to be configured.

        Args:
            table: Table name
            data: Data to insert or update
            id_field: Unique key column used for conflict detection

        Returns:
            Number of affected rows
        """
        if not data:
            raise ValueError("Insert data cannot be empty")

        backend = await self._ensure_backend()
        return await backend.upsert(table, data, id_field)

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
        """Begin a transaction."""
        backend = await self._ensure_backend()
        return await backend.begin_transaction()

    async def commit(self) -> None:
        """Commit the transaction."""
        backend = await self._ensure_backend()
        return await backend.commit()

    async def rollback(self) -> None:
        """Rollback the transaction."""
        backend = await self._ensure_backend()
        return await backend.rollback()

    @asynccontextmanager
    async def transaction(self):
        """Transaction context manager.

        Catches BaseException, not Exception: a client disconnect makes Starlette
        cancel the request task, and `CancelledError` does not inherit from
        `Exception`. With the narrower catch, cancelling mid-transaction skipped
        the rollback entirely, so the connection was never returned to the pool
        and the transaction stayed open on the server until its lock timed out.

        A failing rollback must not mask the original error, so it is suppressed
        and logged — `rollback()` has already released the connection by then.
        """
        await self.begin_transaction()
        try:
            yield
        except BaseException:
            try:
                await self.rollback()
            except Exception as rollback_error:
                logger.error(f"Rollback failed while unwinding a transaction: {rollback_error}")
            raise
        else:
            await self.commit()

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

    async def probe(self) -> None:
        """Round-trip the database, raising if it cannot be reached.

        See `DatabaseBackend.probe` for the cancellation contract.
        """
        backend = await self._ensure_backend()
        await backend.probe()

    async def ping(self) -> bool:
        """Check if the connection is healthy."""
        try:
            await self.probe()
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

    async def __aenter__(self) -> 'AsyncDatabaseClient':
        # Ensure the connection pool is initialized before use
        await self._ensure_backend()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()
