"""
Database Factory - Global database client factory

@file_name: db_factory.py
@author: NetMind.AI
@date: 2025-11-28
@description: Provides a globally shared database client to solve DatabaseClient instance proliferation

=============================================================================
Design Goals
=============================================================================

Problems solved:
- 40+ direct DatabaseClient() calls in code, each creating a new connection
- MCP tools cannot accept externally injected db_client (Agent cannot pass it)
- Uncontrollable connection count, may exhaust database connections

Solution:
- Provides a globally shared AsyncDatabaseClient instance
- All modules obtain the shared instance via get_db_client()
- Supports both synchronous and asynchronous acquisition methods

Usage examples:
    # Async acquisition (recommended)
    db = await get_db_client()

    # Sync acquisition (for synchronous code)
    db = get_db_client_sync()

    # Usage in MCP tools
    @mcp.tool()
    async def job_create(...) -> dict:
        db = await get_db_client()
        module = JobModule(database_client=db)

=============================================================================
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional, TYPE_CHECKING
from loguru import logger

if TYPE_CHECKING:
    from xyz_agent_context.utils.database import AsyncDatabaseClient


# =============================================================================
# URL-based Backend Detection
# =============================================================================

def detect_backend_type(url: str) -> str:
    """
    Detect the database backend type from a URL scheme.

    Args:
        url: Database URL (e.g., 'sqlite:///path/to/db', 'mysql://user:pass@host/db').

    Returns:
        'sqlite' or 'mysql'.

    Raises:
        ValueError: If the URL scheme is not recognized.
    """
    scheme = url.split("://", 1)[0].lower() if "://" in url else ""
    if scheme == "sqlite":
        return "sqlite"
    if scheme in ("mysql", "mysql+mysqlconnector"):
        return "mysql"
    raise ValueError(
        f"Unsupported database URL scheme '{scheme}'. "
        "Use 'sqlite:///path' or 'mysql://user:pass@host/db'."
    )


def parse_sqlite_url(url: str) -> str:
    """
    Extract the file path from a sqlite:// URL.

    Supports both sqlite:///absolute/path and sqlite:///relative/path.
    A special case sqlite:///:memory: returns ':memory:'.

    Args:
        url: A sqlite:// URL.

    Returns:
        The database file path.

    Raises:
        ValueError: If the URL does not start with 'sqlite://'.
    """
    prefix = "sqlite://"
    if not url.lower().startswith(prefix):
        raise ValueError(f"Not a sqlite URL: {url}")
    # Everything after 'sqlite://' is the path (including leading slash for absolute)
    path = url[len(prefix):]
    if not path:
        raise ValueError("sqlite URL must include a path (e.g., sqlite:///path/to/db)")
    return path


# =============================================================================
# Global State
# =============================================================================

_shared_async_client: Optional["AsyncDatabaseClient"] = None
_client_event_loop: Optional[asyncio.AbstractEventLoop] = None  # Record the event loop at creation time
_initialization_lock = asyncio.Lock()


# =============================================================================
# Async Acquisition (Recommended)
# =============================================================================

async def get_db_client() -> "AsyncDatabaseClient":
    """
    Get the shared async database client (recommended method)

    Features:
    - Lazy loading: created on first call
    - Thread-safe: protected by asyncio.Lock
    - Singleton pattern: the entire application shares the same connection pool
    - Event loop aware: automatically recreates the connection pool if the event loop changes

    Returns:
        AsyncDatabaseClient instance

    Example:
        db = await get_db_client()
        result = await db.get_one("users", {"id": 1})
    """
    global _shared_async_client, _client_event_loop

    current_loop = asyncio.get_running_loop()

    # Check if recreation is needed (event loop changed or client is None)
    need_recreate = (
        _shared_async_client is None or
        _client_event_loop is None or
        _client_event_loop != current_loop or
        _client_event_loop.is_closed()
    )

    if need_recreate:
        async with _initialization_lock:
            # Double-check
            need_recreate = (
                _shared_async_client is None or
                _client_event_loop is None or
                _client_event_loop != current_loop or
                _client_event_loop.is_closed()
            )
            if need_recreate:
                # If old client exists and event loop changed, try to close first
                if _shared_async_client is not None and _client_event_loop != current_loop:
                    logger.warning("Event loop changed, recreating AsyncDatabaseClient")
                    # Don't close the old one, as it's in another event loop; just overwrite
                    _shared_async_client = None

                from xyz_agent_context.utils.database import AsyncDatabaseClient
                from xyz_agent_context.settings import settings

                db_url = getattr(settings, 'database_url', None) or ''

                if db_url.startswith('sqlite'):
                    proxy_url = os.environ.get("SQLITE_PROXY_URL", "")

                    if proxy_url:
                        from xyz_agent_context.utils.db_backend_sqlite_proxy import SQLiteProxyBackend

                        logger.info(f"Creating shared AsyncDatabaseClient with SQLite Proxy backend (proxy={proxy_url})")
                        backend = SQLiteProxyBackend(proxy_url)
                        await backend.initialize()
                        _shared_async_client = await AsyncDatabaseClient.create_with_backend(backend)
                    else:
                        from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend

                        db_path = parse_sqlite_url(db_url)
                        logger.info(f"Creating shared AsyncDatabaseClient with SQLite backend (path={db_path})")
                        backend = SQLiteBackend(db_path)
                        await backend.initialize()
                        _shared_async_client = await AsyncDatabaseClient.create_with_backend(backend)
                else:
                    logger.info("Creating shared AsyncDatabaseClient instance")
                    _shared_async_client = await AsyncDatabaseClient.create()

                _client_event_loop = current_loop
                logger.success("Shared AsyncDatabaseClient created successfully")

    return _shared_async_client


# =============================================================================
# Sync Acquisition (for scenarios where await cannot be used)
# =============================================================================

def get_db_client_sync() -> "AsyncDatabaseClient":
    """
    Synchronously get the shared database client

    Note: If the client has not been initialized yet, a new event loop will be
    created for initialization. It is recommended to use get_db_client() instead.

    Returns:
        AsyncDatabaseClient instance

    Example:
        db = get_db_client_sync()
        # Must still be used in an async context afterwards
    """
    global _shared_async_client

    if _shared_async_client is None:
        # Check if in async context
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running event loop, safe to proceed
            loop = None

        if loop is not None:
            raise RuntimeError(
                "get_db_client_sync() cannot be called from async context. "
                "Use 'await get_db_client()' instead."
            )

        from xyz_agent_context.utils.database import AsyncDatabaseClient
        logger.info("Creating shared AsyncDatabaseClient instance (sync)")
        _shared_async_client = asyncio.run(AsyncDatabaseClient.create())
        logger.success("Shared AsyncDatabaseClient created successfully (sync)")

    return _shared_async_client


# =============================================================================
# Management Functions
# =============================================================================

async def close_db_client() -> None:
    """
    Close the shared database client

    Typically called when the application shuts down.
    """
    global _shared_async_client, _client_event_loop

    if _shared_async_client is not None:
        logger.info("Closing shared AsyncDatabaseClient")
        await _shared_async_client.close()
        _shared_async_client = None
        _client_event_loop = None
        logger.success("Shared AsyncDatabaseClient closed")


