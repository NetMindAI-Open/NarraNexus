"""
@file_name: sqlite_proxy_server.py
@author: NexusAgent
@date: 2026-04-08
@description: Standalone SQLite Proxy Server

A dedicated single-process HTTP service that exclusively owns the SQLite database
file. All other processes (Backend, MCP Server, Poller, etc.) access the database
through HTTP calls to this proxy, eliminating multi-process file lock contention.

Usage:
    uv run python -m xyz_agent_context.utils.sqlite_proxy_server

Environment:
    DATABASE_URL: SQLite database URL (e.g., sqlite:///path/to/db)
    SQLITE_PROXY_PORT: Port to listen on (default: 8100)
"""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from loguru import logger
from pydantic import BaseModel

from xyz_agent_context.utils.database import _mysql_to_sqlite_sql
from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend
from xyz_agent_context.utils.db_factory import detect_backend_type, parse_sqlite_url


# =============================================================================
# Request / Response Models
# =============================================================================

class ExecuteRequest(BaseModel):
    query: str
    params: Optional[List[Any]] = None
    txn_id: Optional[str] = None


class GetRequest(BaseModel):
    table: str
    filters: Optional[Dict[str, Any]] = None
    limit: Optional[int] = None
    offset: Optional[int] = None
    order_by: Optional[str] = None
    fields: Optional[List[str]] = None


class GetOneRequest(BaseModel):
    table: str
    filters: Dict[str, Any]


class GetByIdsRequest(BaseModel):
    table: str
    id_field: str
    ids: List[str]


class InsertRequest(BaseModel):
    table: str
    data: Dict[str, Any]
    txn_id: Optional[str] = None


class UpdateRequest(BaseModel):
    table: str
    filters: Dict[str, Any]
    data: Dict[str, Any]
    txn_id: Optional[str] = None


class DeleteRequest(BaseModel):
    table: str
    filters: Dict[str, Any]
    txn_id: Optional[str] = None


class UpsertRequest(BaseModel):
    table: str
    data: Dict[str, Any]
    id_field: str
    txn_id: Optional[str] = None


class TransactionRequest(BaseModel):
    txn_id: Optional[str] = None


class ProxyResponse(BaseModel):
    success: bool
    data: Any = None
    error: Optional[str] = None


# =============================================================================
# Application
# =============================================================================

_backend: Optional[SQLiteBackend] = None


def _get_backend() -> SQLiteBackend:
    if _backend is None:
        raise RuntimeError("SQLite backend not initialized")
    return _backend


# =============================================================================
# Transaction State (cross-process safety)
# =============================================================================
#
# The proxy holds ONE SQLite connection shared by every client process. A
# naive per-client transaction over that shared connection is unsafe:
#
#   P1 — a NON-owner process's write executed while a transaction is open gets
#        folded into that transaction and committed/rolled-back with it
#        (silent data loss for the non-owner).
#   P4 — a client that dies mid-transaction leaves the shared connection stuck
#        in a transaction forever, disabling auto-commit process-wide.
#
# Fix: every transaction gets a server-issued `txn_id`. A write is allowed
# through only if it carries the active token (it is the holder's own write);
# every other write blocks on `_txn_done` until the transaction ends, so it can
# never be folded in. A watchdog force-rolls-back a transaction whose deadline
# has passed, so a dead client can no longer poison the connection.

# Kept well BELOW the client httpx timeout (db_backend_sqlite_proxy.py, 30s):
# an abandoned transaction must be reaped before a write blocked behind it
# hits its own ReadTimeout, so the freed write can still complete. Overridable
# via env for deployments with unusually long legitimate transactions.
_TXN_TIMEOUT: float = float(os.environ.get("SQLITE_PROXY_TXN_TIMEOUT", "10.0"))
_WATCH_INTERVAL: float = float(os.environ.get("SQLITE_PROXY_TXN_WATCH_INTERVAL", "2.0"))

_active_txn: Optional[str] = None
_txn_deadline: float = 0.0
_txn_done: Optional[asyncio.Event] = None
_state_lock: Optional[asyncio.Lock] = None
_watchdog_task: Optional[asyncio.Task] = None


def _reset_txn_state() -> None:
    """(Re)create the transaction primitives on the current running loop.

    asyncio.Event / asyncio.Lock bind to the running loop, so they are created
    here (lifespan startup, and tests) rather than at import time. `_txn_done`
    starts SET, meaning "no transaction in flight — writes may proceed".
    """
    global _active_txn, _txn_deadline, _txn_done, _state_lock
    _active_txn = None
    _txn_deadline = 0.0
    _txn_done = asyncio.Event()
    _txn_done.set()
    _state_lock = asyncio.Lock()


async def _await_txn_turn(txn_id: Optional[str]) -> None:
    """Block a write until it is allowed to run against the shared connection.

    The transaction holder (matching `txn_id`) passes immediately. Any other
    write waits until the active transaction finishes — its write must not land
    inside someone else's transaction. Reads are never gated (see module docs).
    """
    assert _state_lock is not None and _txn_done is not None
    while True:
        async with _state_lock:
            if _active_txn is None or txn_id == _active_txn:
                return
            waiter = _txn_done
        await waiter.wait()


async def _reap_expired_txn() -> None:
    """One watchdog pass: force-rollback an abandoned, past-deadline txn."""
    global _active_txn
    assert _state_lock is not None and _txn_done is not None
    async with _state_lock:
        if _active_txn is None or time.monotonic() <= _txn_deadline:
            return
        stuck = _active_txn
        try:
            await _get_backend().rollback()
        except Exception as e:  # noqa: BLE001 — reaper must always release the flag
            logger.warning(f"Reaper rollback of txn {stuck} failed: {e!r}")
        finally:
            _active_txn = None
            _txn_done.set()
        logger.warning(
            f"Force-rolled-back abandoned proxy transaction {stuck} "
            f"(no commit/rollback within {_TXN_TIMEOUT:.0f}s)"
        )


async def _transaction_watchdog() -> None:
    """Background loop that periodically reaps abandoned transactions.

    Never dies on a transient error (incident lesson #2): a fire-and-forget
    task that swallowed its own exception would silently stop reaping.
    """
    while True:
        try:
            await asyncio.sleep(_WATCH_INTERVAL)
            await _reap_expired_txn()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — watchdog must outlive transient errors
            logger.warning(f"Transaction watchdog pass failed: {e!r}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize SQLite backend and run schema migration on startup."""
    global _backend

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        logger.error("DATABASE_URL environment variable is required")
        sys.exit(1)

    if detect_backend_type(db_url) != "sqlite":
        logger.error("SQLite Proxy only supports sqlite:// URLs")
        sys.exit(1)

    db_path = parse_sqlite_url(db_url)
    logger.info(f"SQLite Proxy starting with database: {db_path}")

    _backend = SQLiteBackend(db_path)
    await _backend.initialize()
    logger.info("SQLite backend initialized")

    # Run schema migration
    from xyz_agent_context.utils.schema_registry import auto_migrate
    await auto_migrate(_backend)
    logger.info("Schema auto-migration complete")

    # Initialize transaction state on this loop and start the abandoned-txn reaper.
    global _watchdog_task
    _reset_txn_state()
    _watchdog_task = asyncio.create_task(_transaction_watchdog())

    yield

    # Shutdown
    logger.info("SQLite Proxy shutting down...")
    if _watchdog_task is not None:
        _watchdog_task.cancel()
        try:
            await _watchdog_task
        except asyncio.CancelledError:
            pass
        _watchdog_task = None
    await _backend.close()
    _backend = None
    logger.info("SQLite Proxy stopped")


app = FastAPI(
    title="SQLite Proxy Server",
    description="Single-process SQLite access proxy to eliminate multi-process lock contention",
    version="1.0.0",
    lifespan=lifespan,
)


# =============================================================================
# Health Check
# =============================================================================

@app.get("/health")
async def health():
    backend = _get_backend()
    try:
        result = await backend.execute("SELECT 1")
        return {"status": "ok", "dialect": "sqlite"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


# =============================================================================
# Raw SQL Execution
# =============================================================================

@app.post("/execute")
async def execute(req: ExecuteRequest):
    backend = _get_backend()
    try:
        # /execute carries raw SQL that MAY be a write (a caller passing an
        # UPDATE/DELETE with fetch=True routes here — e.g. ModulePoller, a
        # separate process). We cannot cheaply prove it read-only, so gate it
        # like the write endpoints; otherwise a non-holder process's write
        # would fold into an open transaction. Cost: raw SELECTs serialize
        # during a (short) transaction — an accepted trade to close the hole.
        await _await_txn_turn(req.txn_id)
        query = _mysql_to_sqlite_sql(req.query)
        params = tuple(req.params) if req.params else None
        rows = await backend.execute(query, params)
        return ProxyResponse(success=True, data=_serialize_rows(rows))
    except Exception as e:
        logger.exception(f"execute error: {e}")
        return ProxyResponse(success=False, error=str(e))


@app.post("/execute_write")
async def execute_write(req: ExecuteRequest):
    backend = _get_backend()
    try:
        await _await_txn_turn(req.txn_id)
        query = _mysql_to_sqlite_sql(req.query)
        params = tuple(req.params) if req.params else None
        affected = await backend.execute_write(query, params)
        return ProxyResponse(success=True, data=affected)
    except Exception as e:
        logger.exception(f"execute_write error: {e}")
        return ProxyResponse(success=False, error=str(e))


# =============================================================================
# CRUD Operations
# =============================================================================

@app.post("/get")
async def get(req: GetRequest):
    backend = _get_backend()
    try:
        rows = await backend.get(
            table=req.table,
            filters=req.filters,
            limit=req.limit,
            offset=req.offset,
            order_by=req.order_by,
            fields=req.fields,
        )
        return ProxyResponse(success=True, data=_serialize_rows(rows))
    except Exception as e:
        logger.exception(f"get error: {e}")
        return ProxyResponse(success=False, error=str(e))


@app.post("/get_one")
async def get_one(req: GetOneRequest):
    backend = _get_backend()
    try:
        row = await backend.get_one(req.table, req.filters)
        return ProxyResponse(success=True, data=_serialize_row(row) if row else None)
    except Exception as e:
        logger.exception(f"get_one error: {e}")
        return ProxyResponse(success=False, error=str(e))


@app.post("/get_by_ids")
async def get_by_ids(req: GetByIdsRequest):
    backend = _get_backend()
    try:
        rows = await backend.get_by_ids(req.table, req.id_field, req.ids)
        return ProxyResponse(
            success=True,
            data=[_serialize_row(r) if r else None for r in rows],
        )
    except Exception as e:
        logger.exception(f"get_by_ids error: {e}")
        return ProxyResponse(success=False, error=str(e))


@app.post("/insert")
async def insert(req: InsertRequest):
    backend = _get_backend()
    try:
        await _await_txn_turn(req.txn_id)
        lastrowid = await backend.insert(req.table, req.data)
        return ProxyResponse(success=True, data=lastrowid)
    except Exception as e:
        logger.exception(f"insert error: {e}")
        return ProxyResponse(success=False, error=str(e))


@app.post("/update")
async def update(req: UpdateRequest):
    backend = _get_backend()
    try:
        await _await_txn_turn(req.txn_id)
        affected = await backend.update(req.table, req.filters, req.data)
        return ProxyResponse(success=True, data=affected)
    except Exception as e:
        logger.exception(f"update error: {e}")
        return ProxyResponse(success=False, error=str(e))


@app.post("/delete")
async def delete(req: DeleteRequest):
    backend = _get_backend()
    try:
        await _await_txn_turn(req.txn_id)
        affected = await backend.delete(req.table, req.filters)
        return ProxyResponse(success=True, data=affected)
    except Exception as e:
        logger.exception(f"delete error: {e}")
        return ProxyResponse(success=False, error=str(e))


@app.post("/upsert")
async def upsert(req: UpsertRequest):
    backend = _get_backend()
    try:
        await _await_txn_turn(req.txn_id)
        affected = await backend.upsert(req.table, req.data, req.id_field)
        return ProxyResponse(success=True, data=affected)
    except Exception as e:
        logger.exception(f"upsert error: {e}")
        return ProxyResponse(success=False, error=str(e))


# =============================================================================
# Transaction Support (token-gated, cross-process safe)
# =============================================================================
#
# Only one transaction runs at a time. `begin` issues a `txn_id` the client
# must present on every write and on commit/rollback; concurrent writes from
# other clients block until the transaction ends (see _await_txn_turn). An
# abandoned transaction is reaped by the watchdog. See the Transaction State
# section above for the full rationale.

@app.post("/transaction/begin")
async def transaction_begin():
    global _active_txn, _txn_deadline
    assert _state_lock is not None and _txn_done is not None
    backend = _get_backend()
    try:
        while True:
            await _txn_done.wait()  # wait until no transaction is in flight
            async with _state_lock:
                if _active_txn is not None:
                    continue  # lost the race — another begin won; wait again
                txn_id = "ptxn_" + secrets.token_hex(4)
                # Claim the slot BEFORE issuing BEGIN so a concurrent foreign
                # write can never slip in between (it sees _active_txn set).
                _active_txn = txn_id
                _txn_done.clear()
                _txn_deadline = time.monotonic() + _TXN_TIMEOUT
                try:
                    await backend.begin_transaction()
                except Exception:
                    _active_txn = None
                    _txn_done.set()
                    raise
                return ProxyResponse(success=True, data={"txn_id": txn_id})
    except Exception as e:
        return ProxyResponse(success=False, error=str(e))


async def _end_transaction(txn_id: Optional[str], *, commit: bool) -> ProxyResponse:
    """Commit or rollback the active transaction, always releasing the slot."""
    global _active_txn
    assert _state_lock is not None and _txn_done is not None
    async with _state_lock:
        if txn_id is None or txn_id != _active_txn:
            return ProxyResponse(success=False, error="unknown or expired transaction")
        error: Optional[str] = None
        try:
            if commit:
                await _get_backend().commit()
            else:
                await _get_backend().rollback()
        except Exception as e:  # noqa: BLE001 — surface the error but still release
            error = str(e)
        finally:
            _active_txn = None
            _txn_done.set()
    if error is not None:
        return ProxyResponse(success=False, error=error)
    return ProxyResponse(success=True)


@app.post("/transaction/commit")
async def transaction_commit(req: TransactionRequest):
    return await _end_transaction(req.txn_id, commit=True)


@app.post("/transaction/rollback")
async def transaction_rollback(req: TransactionRequest):
    return await _end_transaction(req.txn_id, commit=False)


# =============================================================================
# Serialization Helpers
# =============================================================================

def _serialize_row(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Ensure all values in a row are JSON-serializable."""
    if row is None:
        return None
    result = {}
    for key, value in row.items():
        if hasattr(value, "isoformat"):
            result[key] = value.isoformat()
        else:
            result[key] = value
    return result


def _serialize_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Serialize a list of rows."""
    return [_serialize_row(r) for r in rows]


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("SQLITE_PROXY_PORT", "8100"))
    logger.info(f"Starting SQLite Proxy Server on port {port}")
    uvicorn.run(
        "xyz_agent_context.utils.sqlite_proxy_server:app",
        host="127.0.0.1",
        port=port,
        log_level="info",
    )
