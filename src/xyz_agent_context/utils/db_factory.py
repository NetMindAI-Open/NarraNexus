"""
Database Factory - Per-event-loop database client registry

@file_name: db_factory.py
@author: NetMind.AI
@date: 2025-11-28
@description: Provides a database client keyed by the running asyncio event loop.

=============================================================================
Design Goals
=============================================================================

Problems solved:
- 40+ direct DatabaseClient() calls in code, each creating a new connection
- MCP tools cannot accept externally injected db_client (Agent cannot pass it)
- Uncontrollable connection count, may exhaust database connections
- Cross-loop pool misuse: a single process-wide singleton breaks when the
  MCP container runs each module in its own threaded event loop -- the
  aiomysql pool binds its internal Futures (e.g. Pool._wakeup) to the loop
  that created it, and reusing that pool from another loop raises
  "got Future attached to a different loop". The earlier mitigation
  (evict + recreate on loop change) only pushed the problem around:
  whichever loop lost the race held stale Futures until the next access.

Solution:
- One AsyncDatabaseClient per event loop (keyed by id(loop))
- Each loop builds its own pool, lives as long as the loop is alive
- Closed loops are evicted on every access (cheap O(n) over active loops)
- A per-loop asyncio.Lock serialises concurrent first-call on the same loop
- Legacy `get_db_client_sync` path preserved as an escape hatch for
  bootstrap code (its returned client must not be reused from async code)

Usage examples:
    # Async acquisition (recommended)
    db = await get_db_client()

    # Sync acquisition (bootstrap only; never reuse result from async code)
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
from typing import Dict, Optional, TYPE_CHECKING

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
# Per-loop state
# =============================================================================
#
# Key: id(loop). Loops do not have a reliable stable identifier other than
# their Python object id(); we pair every entry with a reference in
# _loops_by_id so we can detect closed loops and evict them before id()
# potentially gets reused by a new loop object at the same address.
#
# We intentionally keep a strong reference to each loop. Active loops are
# held by their thread anyway; closed loops get evicted on the next access.

SYNC_KEY: int = -1  # pseudo loop-id for the sync bootstrap path

_clients_by_loop: Dict[int, "AsyncDatabaseClient"] = {}
_locks_by_loop: Dict[int, asyncio.Lock] = {}
_loops_by_id: Dict[int, asyncio.AbstractEventLoop] = {}


# =============================================================================
# Async Acquisition (Recommended)
# =============================================================================

async def get_db_client() -> "AsyncDatabaseClient":
    """
    Get the AsyncDatabaseClient bound to the currently running event loop.

    Features:
    - One pool per event loop (no cross-loop Future leaks)
    - Lazy: the pool for a given loop is built on that loop's first call
    - Thread-safe: serialised by a per-loop asyncio.Lock
    - Self-evicting: closed loops are dropped on every access

    Returns:
        AsyncDatabaseClient instance bound to the current running loop.

    Example:
        db = await get_db_client()
        result = await db.get_one("users", {"id": 1})
    """
    current_loop = asyncio.get_running_loop()
    loop_id = id(current_loop)

    # Cheap housekeeping — O(n) in number of active loops (typically < 10).
    _evict_closed_loops()

    existing = _clients_by_loop.get(loop_id)
    if existing is not None:
        return existing

    # First call on this loop — race-safe creation via per-loop lock.
    lock = _locks_by_loop.get(loop_id)
    if lock is None:
        # Constructing asyncio.Lock() while *this* loop is running binds it
        # to this loop, which is what we want.
        lock = asyncio.Lock()
        _locks_by_loop[loop_id] = lock
        _loops_by_id[loop_id] = current_loop

    async with lock:
        existing = _clients_by_loop.get(loop_id)
        if existing is not None:
            return existing

        client = await _build_client_for_current_loop()
        _clients_by_loop[loop_id] = client
        logger.info(
            f"AsyncDatabaseClient created for loop id={loop_id} "
            f"(active loops: {len(_clients_by_loop)})"
        )
        return client


async def _build_client_for_current_loop() -> "AsyncDatabaseClient":
    """Construct a fresh AsyncDatabaseClient on the currently running loop.

    Extracted from get_db_client() so the branching stays readable. All
    imports are local to avoid circular-import issues at package load.
    """
    from xyz_agent_context.utils.database import AsyncDatabaseClient
    from xyz_agent_context.settings import settings

    db_url = getattr(settings, 'database_url', None) or ''

    if db_url.startswith('sqlite'):
        proxy_url = os.environ.get("SQLITE_PROXY_URL", "")

        if proxy_url:
            from xyz_agent_context.utils.db_backend_sqlite_proxy import SQLiteProxyBackend

            logger.info(
                f"Creating AsyncDatabaseClient with SQLite Proxy backend (proxy={proxy_url})"
            )
            backend = SQLiteProxyBackend(proxy_url)
            await backend.initialize()
            return await AsyncDatabaseClient.create_with_backend(backend)

        from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend

        db_path = parse_sqlite_url(db_url)
        logger.info(f"Creating AsyncDatabaseClient with SQLite backend (path={db_path})")
        backend = SQLiteBackend(db_path)
        await backend.initialize()
        return await AsyncDatabaseClient.create_with_backend(backend)

    from xyz_agent_context.utils.db_backend_mysql import MySQLBackend
    from xyz_agent_context.utils.database import load_db_config

    db_config = load_db_config()
    logger.info(
        f"Creating AsyncDatabaseClient with MySQL backend (host={db_config.get('host')})"
    )
    backend = MySQLBackend(db_config)
    await backend.initialize()
    return await AsyncDatabaseClient.create_with_backend(backend)


def _evict_closed_loops() -> None:
    """Drop dict entries whose loop has been closed.

    Important for long-running processes that spawn short-lived loops
    (e.g. test harnesses, one-shot migration scripts). Without this, the
    entry would linger and a new loop later allocated at the same memory
    address could accidentally collide on id().
    """
    stale_ids = [loop_id for loop_id, loop in _loops_by_id.items() if loop.is_closed()]
    for loop_id in stale_ids:
        _clients_by_loop.pop(loop_id, None)
        _locks_by_loop.pop(loop_id, None)
        _loops_by_id.pop(loop_id, None)
        logger.info(f"Evicted DB client for closed loop id={loop_id}")


# =============================================================================
# Sync Acquisition (bootstrap only)
# =============================================================================

def get_db_client_sync() -> "AsyncDatabaseClient":
    """
    Synchronously get a database client (BOOTSTRAP ONLY).

    Caution: the returned client is built via asyncio.run(), which creates
    and tears down a temporary event loop. Any subsequent async call that
    tries to use it will fail with "Event loop is closed". This path is
    retained only for code paths that run before any asyncio loop exists
    (sync module imports, top-level scripts). Prefer `await get_db_client()`
    everywhere else.

    Returns:
        AsyncDatabaseClient instance (cached under SYNC_KEY=-1).

    Raises:
        RuntimeError: if called from inside a running event loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass  # no running loop — safe to proceed
    else:
        raise RuntimeError(
            "get_db_client_sync() cannot be called from async context. "
            "Use 'await get_db_client()' instead."
        )

    cached = _clients_by_loop.get(SYNC_KEY)
    if cached is not None:
        return cached

    from xyz_agent_context.utils.database import AsyncDatabaseClient

    logger.info("Creating AsyncDatabaseClient instance (sync bootstrap)")
    client = asyncio.run(AsyncDatabaseClient.create())
    _clients_by_loop[SYNC_KEY] = client
    logger.info("AsyncDatabaseClient created (sync bootstrap)")
    return client


# =============================================================================
# Management Functions
# =============================================================================

async def close_db_client() -> None:
    """
    Close every per-loop database client.

    Typically called when the application shuts down. For each client we
    try to schedule the close on its origin loop via
    `asyncio.run_coroutine_threadsafe` — closing a client from the wrong
    loop would trigger the same cross-loop errors we're trying to avoid.
    If the origin loop is already closed, we drop the entry without
    awaiting close (its OS resources will be reclaimed on process exit).
    """
    for loop_id, client in list(_clients_by_loop.items()):
        loop = _loops_by_id.get(loop_id)
        try:
            if loop is None or loop.is_closed():
                logger.info(
                    f"Skipping close for loop id={loop_id}: origin loop already gone"
                )
            else:
                current = _safe_get_running_loop()
                if current is loop:
                    await client.close()
                else:
                    # Called from a different loop (or no loop at all) —
                    # dispatch onto the origin loop and wait briefly.
                    fut = asyncio.run_coroutine_threadsafe(client.close(), loop)
                    fut.result(timeout=5)
                logger.info(f"Closed AsyncDatabaseClient for loop id={loop_id}")
        except Exception as e:  # noqa: BLE001 — best-effort shutdown
            logger.warning(
                f"Failed to close AsyncDatabaseClient for loop id={loop_id}: {e!r}"
            )

    _clients_by_loop.clear()
    _locks_by_loop.clear()
    _loops_by_id.clear()


def _safe_get_running_loop() -> Optional[asyncio.AbstractEventLoop]:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None
