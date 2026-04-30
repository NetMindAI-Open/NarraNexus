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

import os
import sys
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


class UpdateRequest(BaseModel):
    table: str
    filters: Dict[str, Any]
    data: Dict[str, Any]


class DeleteRequest(BaseModel):
    table: str
    filters: Dict[str, Any]


class UpsertRequest(BaseModel):
    table: str
    data: Dict[str, Any]
    id_field: str


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

    yield

    # Shutdown
    logger.info("SQLite Proxy shutting down...")
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
        lastrowid = await backend.insert(req.table, req.data)
        return ProxyResponse(success=True, data=lastrowid)
    except Exception as e:
        logger.exception(f"insert error: {e}")
        return ProxyResponse(success=False, error=str(e))


@app.post("/update")
async def update(req: UpdateRequest):
    backend = _get_backend()
    try:
        affected = await backend.update(req.table, req.filters, req.data)
        return ProxyResponse(success=True, data=affected)
    except Exception as e:
        logger.exception(f"update error: {e}")
        return ProxyResponse(success=False, error=str(e))


@app.post("/delete")
async def delete(req: DeleteRequest):
    backend = _get_backend()
    try:
        affected = await backend.delete(req.table, req.filters)
        return ProxyResponse(success=True, data=affected)
    except Exception as e:
        logger.exception(f"delete error: {e}")
        return ProxyResponse(success=False, error=str(e))


@app.post("/upsert")
async def upsert(req: UpsertRequest):
    backend = _get_backend()
    try:
        affected = await backend.upsert(req.table, req.data, req.id_field)
        return ProxyResponse(success=True, data=affected)
    except Exception as e:
        logger.exception(f"upsert error: {e}")
        return ProxyResponse(success=False, error=str(e))


# =============================================================================
# Transaction Support (limited — for schema migration only)
# =============================================================================

@app.post("/transaction/begin")
async def transaction_begin():
    backend = _get_backend()
    try:
        await backend.begin_transaction()
        return ProxyResponse(success=True)
    except Exception as e:
        return ProxyResponse(success=False, error=str(e))


@app.post("/transaction/commit")
async def transaction_commit():
    backend = _get_backend()
    try:
        await backend.commit()
        return ProxyResponse(success=True)
    except Exception as e:
        return ProxyResponse(success=False, error=str(e))


@app.post("/transaction/rollback")
async def transaction_rollback():
    backend = _get_backend()
    try:
        await backend.rollback()
        return ProxyResponse(success=True)
    except Exception as e:
        return ProxyResponse(success=False, error=str(e))


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
