"""
@file_name: db_backend_sqlite_proxy.py
@author: NexusAgent
@date: 2026-04-08
@description: SQLite Proxy Backend - DatabaseBackend implementation via HTTP proxy

Implements the DatabaseBackend interface by forwarding all operations to the
SQLite Proxy Server via HTTP. This eliminates multi-process SQLite file lock
contention by ensuring only the proxy process directly accesses the database.

Usage:
    backend = SQLiteProxyBackend("http://localhost:8100")
    await backend.initialize()
    # Use like any other DatabaseBackend
    row = await backend.get_one("users", {"id": "user1"})
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from xyz_agent_context.utils.db_backend import DatabaseBackend


class SQLiteProxyBackend(DatabaseBackend):
    """
    DatabaseBackend that delegates all operations to the SQLite Proxy Server.

    All read and write operations are forwarded via HTTP POST to the proxy,
    which holds the exclusive SQLite connection. This converts multi-process
    file lock contention into serialized HTTP requests.

    Args:
        proxy_url: Base URL of the SQLite Proxy Server (e.g., "http://localhost:8100").
        timeout: HTTP request timeout in seconds.
    """

    def __init__(self, proxy_url: str, timeout: float = 30.0) -> None:
        self._proxy_url = proxy_url.rstrip("/")
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

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
        Initialize the HTTP client and verify proxy connectivity.

        Retries connection to the proxy with exponential backoff.
        """
        self._client = httpx.AsyncClient(
            base_url=self._proxy_url,
            timeout=self._timeout,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

        # Wait for proxy to be ready with retries
        max_retries = 15
        for attempt in range(max_retries):
            try:
                resp = await self._client.get("/health")
                if resp.status_code == 200:
                    logger.success(f"Connected to SQLite Proxy at {self._proxy_url}")
                    return
            except (httpx.ConnectError, httpx.ReadError):
                pass

            if attempt < max_retries - 1:
                wait = min(0.5 * (2 ** attempt), 5.0)
                logger.info(
                    f"Waiting for SQLite Proxy at {self._proxy_url} "
                    f"(attempt {attempt + 1}/{max_retries}, retry in {wait:.1f}s)"
                )
                await asyncio.sleep(wait)

        raise ConnectionError(
            f"SQLite Proxy not reachable at {self._proxy_url} after {max_retries} attempts. "
            "Ensure the proxy is started before other services."
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            # Lazy initialization: create client on first use if initialize() wasn't called
            self._client = httpx.AsyncClient(
                base_url=self._proxy_url,
                timeout=self._timeout,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return self._client

    async def _post(self, path: str, payload: dict) -> Any:
        """Send a POST request to the proxy and return the data field."""
        client = self._ensure_client()
        resp = await client.post(path, json=payload)
        body = resp.json()
        if not body.get("success"):
            error_msg = body.get("error", "Unknown proxy error")
            raise RuntimeError(f"SQLite Proxy error ({path}): {error_msg}")
        return body.get("data")

    # ===== Raw SQL Execution =====

    async def execute(
        self,
        query: str,
        params: Optional[tuple] = None,
    ) -> List[Dict[str, Any]]:
        """Execute a raw SQL query via the proxy."""
        return await self._post("/execute", {
            "query": query,
            "params": [_prepare_value(p) for p in params] if params else None,
        })

    async def execute_write(
        self,
        query: str,
        params: Optional[tuple] = None,
    ) -> int:
        """Execute a write SQL statement via the proxy."""
        return await self._post("/execute_write", {
            "query": query,
            "params": [_prepare_value(p) for p in params] if params else None,
        })

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
        """Query rows from a table via the proxy."""
        return await self._post("/get", {
            "table": table,
            "filters": _prepare_filters(filters),
            "limit": limit,
            "offset": offset,
            "order_by": order_by,
            "fields": fields,
        })

    async def get_one(
        self,
        table: str,
        filters: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Query a single row via the proxy."""
        return await self._post("/get_one", {
            "table": table,
            "filters": _prepare_filters(filters),
        })

    async def get_by_ids(
        self,
        table: str,
        id_field: str,
        ids: List[str],
    ) -> List[Optional[Dict[str, Any]]]:
        """Batch-fetch rows by IDs via the proxy."""
        return await self._post("/get_by_ids", {
            "table": table,
            "id_field": id_field,
            "ids": ids,
        })

    async def insert(
        self,
        table: str,
        data: Dict[str, Any],
    ) -> int:
        """Insert a row via the proxy."""
        return await self._post("/insert", {
            "table": table,
            "data": _prepare_data(data),
        })

    async def update(
        self,
        table: str,
        filters: Dict[str, Any],
        data: Dict[str, Any],
    ) -> int:
        """Update rows via the proxy."""
        return await self._post("/update", {
            "table": table,
            "filters": _prepare_filters(filters),
            "data": _prepare_data(data),
        })

    async def delete(
        self,
        table: str,
        filters: Dict[str, Any],
    ) -> int:
        """Delete rows via the proxy."""
        return await self._post("/delete", {
            "table": table,
            "filters": _prepare_filters(filters),
        })

    async def upsert(
        self,
        table: str,
        data: Dict[str, Any],
        id_field: str,
    ) -> int:
        """Upsert a row via the proxy."""
        return await self._post("/upsert", {
            "table": table,
            "data": _prepare_data(data),
            "id_field": id_field,
        })

    # ===== Transaction Support =====

    async def begin_transaction(self) -> None:
        """Begin a transaction on the proxy."""
        await self._post("/transaction/begin", {})

    async def commit(self) -> None:
        """Commit the transaction on the proxy."""
        await self._post("/transaction/commit", {})

    async def rollback(self) -> None:
        """Rollback the transaction on the proxy."""
        await self._post("/transaction/rollback", {})


# =============================================================================
# Value Serialization Helpers
# =============================================================================

def _prepare_value(value: Any) -> Any:
    """Serialize a Python value for JSON transport to the proxy."""
    if isinstance(value, bool):
        return 1 if value else 0
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        import json
        return json.dumps(value, ensure_ascii=False)
    return value


def _prepare_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Serialize all values in a data dict for transport."""
    return {k: _prepare_value(v) for k, v in data.items()}


def _prepare_filters(filters: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Serialize filter values for transport."""
    if filters is None:
        return None
    return {k: _prepare_value(v) for k, v in filters.items()}
