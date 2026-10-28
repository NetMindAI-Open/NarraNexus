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
- Cross-loop pool misuse: a single process-wide singleton breaks the moment a
  second event loop touches it -- the aiomysql pool binds its internal Futures
  (e.g. Pool._wakeup) to the loop that created it, and reusing that pool from
  another loop raises "got Future attached to a different loop". The earlier
  mitigation (evict + recreate on loop change) only pushed the problem around:
  whichever loop lost the race held stale Futures until the next access.
  (2026-08-17: this used to read "the MCP container runs each module in its own
  threaded event loop". It does not -- `module_runner.py` gives each module its
  own PROCESS with one process-lifetime `asyncio.run`. The real multi-loop
  sources are `get_db_client_sync`'s throwaway `asyncio.run`, `lark_trigger`'s
  per-reconnect loop, one-shot scripts and the test harness. The mechanism the
  paragraph describes is unchanged; only the example was wrong, and it was
  sending readers into MCP code to look for something that is not there.)

Solution:
- One AsyncDatabaseClient per event loop (keyed by id(loop))
- Each loop builds its own pool, lives as long as the loop is alive
- Closed loops are CLOSED and evicted on every access (O(n) over active
  loops, under one bounded budget -- see _evict_closed_loops)
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
    from narranexus.platform.utils.db.database import AsyncDatabaseClient


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
    # Collapse a leading run of slashes to one. Callers build the URL as
    # `sqlite:///` + an already-absolute path, yielding `sqlite:////Users/...`
    # (SQLAlchemy's 4-slash absolute form); stripping only `sqlite://` left a
    # malformed `//Users/...`. macOS tolerates it, but it's wrong and shows up
    # in logs as `database: //Users/...`. `:memory:` and relative paths untouched.
    if path.startswith("//"):
        path = "/" + path.lstrip("/")
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
    - Self-evicting: closed loops are closed + dropped on every access

    Returns:
        AsyncDatabaseClient instance bound to the current running loop.

    Example:
        db = await get_db_client()
        result = await db.get_one("users", {"id": 1})
    """
    current_loop = asyncio.get_running_loop()
    loop_id = id(current_loop)

    # Cheap housekeeping — O(n) in number of active loops (typically < 10).
    await _evict_closed_loops()

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
    from narranexus.platform.utils.db.database import AsyncDatabaseClient
    from narranexus.platform.settings import settings

    db_url = getattr(settings, 'database_url', None) or ''

    if db_url.startswith('sqlite'):
        proxy_url = os.environ.get("SQLITE_PROXY_URL", "")

        if proxy_url:
            from narranexus.platform.utils.db.db_backend_sqlite_proxy import SQLiteProxyBackend

            logger.info(
                f"Creating AsyncDatabaseClient with SQLite Proxy backend (proxy={proxy_url})"
            )
            backend = SQLiteProxyBackend(proxy_url)
            await backend.initialize()
            return await AsyncDatabaseClient.create_with_backend(backend)

        from narranexus.platform.utils.db.db_backend_sqlite import SQLiteBackend

        db_path = parse_sqlite_url(db_url)
        logger.info(f"Creating AsyncDatabaseClient with SQLite backend (path={db_path})")
        backend = SQLiteBackend(db_path)
        await backend.initialize()
        return await AsyncDatabaseClient.create_with_backend(backend)

    from narranexus.platform.utils.db.db_backend_mysql import MySQLBackend
    from narranexus.platform.utils.db.database import load_db_config

    db_config = load_db_config()
    # Pool size is env-tunable (MYSQL_POOL_SIZE, default 10). The worker
    # supervisor drives poller + jobs + bus + every channel trigger from ONE
    # process = ONE pool, where the pre-consolidation layout had 4 processes ×
    # their own pools. A supervisor deployment should raise this (>=25) so the
    # combined worker/subscriber concurrency does not starve on pool.acquire().
    try:
        pool_size = max(1, int(os.environ.get("MYSQL_POOL_SIZE", "10")))
    except ValueError:
        pool_size = 10
    logger.info(
        f"Creating AsyncDatabaseClient with MySQL backend "
        f"(host={db_config.get('host')}, pool_size={pool_size})"
    )
    backend = MySQLBackend(db_config, pool_size=pool_size)
    await backend.initialize()
    return await AsyncDatabaseClient.create_with_backend(backend)


# One budget for the WHOLE eviction sweep. Per-entry it would be unbounded in
# aggregate, and this sweep sits in front of `get_db_client()`'s cached-hit
# return — the hottest DB entry point in the process.
_EVICT_SWEEP_BUDGET: float = 5.0


async def _evict_closed_loops() -> None:
    """Close, then drop, every entry whose loop has been closed.

    Important for long-running processes that spawn short-lived loops — the
    test harness, one-shot scripts, `lark_trigger`'s per-reconnect loop, and
    `get_db_client_sync`'s own `asyncio.run`. Without this, the entry would
    linger and a new loop later allocated at the same memory address could
    accidentally collide on id().

    **Forgetting the client is not the same as releasing it.** An evicted
    client still owns a live backend connection — for SQLite, an aiosqlite
    worker thread holding an open handle on the database file, and, if the
    loop died mid-write, an open WRITE transaction that nothing will ever
    commit or roll back. The WAL write lock is then held for the remaining
    life of the process, and every later writer pays
    `_MAX_WRITE_RETRIES` (10) x `busy_timeout` (30s) = ~5 minutes of silent
    blocking before it finally raises "database is locked" (2026-08-17: this
    was 92% of the test suite's 38-minute wall clock, and it is the same
    shape as a local desktop install wedging its own writes).

    The origin loop is closed by definition here, so the close runs on the
    CURRENT loop — aiosqlite needs *a* running loop to close, not the one that
    opened the connection (same reasoning as `close_db_client`).

    Two honest limits, both worth stating because the headline above oversells
    without them:

    * **Reclamation is effective for SQLite only.** `MySQLBackend.close()` goes
      through `pool.wait_closed()`, which drives `conn.close()` →
      `loop.call_soon(...)` on the dead loop and waits on a `Condition` bound to
      it. On MySQL this reliably fails rather than reclaiming, and the warning
      below is all you get. Not yet verified against a live MySQL, so no fix is
      claimed here — for MySQL this function still only stops the id() collision.
    * **`SYNC_KEY` is out of reach.** `get_db_client_sync()` registers under
      `_clients_by_loop[SYNC_KEY]` and never touches `_loops_by_id`, which is
      what `stale_ids` is derived from — so the one client guaranteed to have a
      dead loop is structurally excluded. Only `close_db_client()` reclaims it.
      Making it evictable means the first `await get_db_client()` in a process
      would close the bootstrap client out from under whoever still holds it;
      that needs its own audit of `get_db_client_sync` callers.

    Bounded by ONE deadline for the whole sweep, not per entry: `stale_ids` is
    as long as the caller's loop churn made it, and this runs on every
    acquisition ahead of the cached-hit return — a per-entry timeout would let
    whoever churns loops set the latency paid by whoever next asks for a client.
    """
    stale_ids = [loop_id for loop_id, loop in _loops_by_id.items() if loop.is_closed()]
    if not stale_ids:
        return

    running = asyncio.get_running_loop()
    deadline = running.time() + _EVICT_SWEEP_BUDGET
    for loop_id in stale_ids:
        # Pop BEFORE awaiting. This is what makes two coroutines on the same
        # loop safe against double-closing the same client; do not "tidy" these
        # to after the await.
        client = _clients_by_loop.pop(loop_id, None)
        _locks_by_loop.pop(loop_id, None)
        _loops_by_id.pop(loop_id, None)
        remaining = deadline - running.time()
        if client is not None and remaining > 0:
            try:
                await asyncio.wait_for(client.close(), timeout=remaining)
            except Exception as e:  # noqa: BLE001 — best-effort reclamation
                logger.warning(
                    f"Evicted DB client for closed loop id={loop_id} but could "
                    f"not close its connection: {e!r}"
                )
        elif client is not None:
            # Out of budget: the entry is still dropped (the id() collision is
            # the part that must not survive), the connection is not reclaimed,
            # and we say so rather than looking successful.
            logger.warning(
                f"Evicted DB client for closed loop id={loop_id} WITHOUT closing "
                f"it — sweep budget of {_EVICT_SWEEP_BUDGET}s exhausted over "
                f"{len(stale_ids)} stale loops"
            )
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

    from narranexus.platform.utils.db.database import AsyncDatabaseClient

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
    If the origin loop is already closed, we close on the CURRENT loop
    instead. aiosqlite only needs *a* running loop for close(), not the
    origin loop, so the cross-loop concern doesn't apply once the origin
    loop is gone.

    **Why this call is still load-bearing (2026-08-17).** It used to be
    justified by "the aiosqlite worker is a NON-daemon thread and blocks
    interpreter shutdown forever unless closed" — pytest printing its
    summary and then hanging. `db_backend_sqlite` now makes that worker a
    DAEMON thread, so that hang is gone and this call can no longer be
    defended on those grounds. It is still required for a different and
    more important reason: a daemon thread is KILLED at interpreter exit,
    wherever it happens to be. This close is the only point at which the
    connection's writes get drained and its SQLite locks released on
    purpose rather than by process death. Do not delete it because the
    hang stopped happening.
    """
    for loop_id, client in list(_clients_by_loop.items()):
        loop = _loops_by_id.get(loop_id)
        try:
            if loop is None or loop.is_closed():
                logger.info(
                    f"Origin loop id={loop_id} already gone — closing client "
                    f"on the current loop to stop its worker thread"
                )
                await client.close()
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
