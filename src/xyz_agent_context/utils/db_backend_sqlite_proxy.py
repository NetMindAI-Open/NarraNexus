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
import contextvars
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from xyz_agent_context.utils.db_backend import DatabaseBackend


# Active transaction token, scoped to the coroutine that opened the transaction.
#
# It MUST NOT live on the backend instance: db_factory hands one
# AsyncDatabaseClient (hence one SQLiteProxyBackend) to every coroutine on an
# event loop, so an instance attribute would be shared by all concurrent
# requests in that process — a transaction opened by one (e.g. wipe_service)
# would stamp its token onto every OTHER coroutine's writes, and the proxy
# would admit them as the holder's own writes and fold them into that
# transaction. A ContextVar is copied per asyncio Task, so only the task that
# called begin_transaction (and coroutines it awaits) carries the token;
# independent request tasks see None and are correctly gated by the proxy.
_current_txn: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "sqlite_proxy_current_txn", default=None
)


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
        # trust_env=False: ignore HTTP_PROXY / HTTPS_PROXY / NO_PROXY env vars.
        # The proxy is on loopback (127.0.0.1:8100). Users running VPN clients
        # (clash, v2ray, etc.) typically set http_proxy=127.0.0.1:7897 for
        # outbound traffic — that local SOCKS/HTTP proxy refuses loopback
        # destinations, so without trust_env=False the backend dies trying to
        # tunnel its own DB calls through the user's VPN.
        self._client = httpx.AsyncClient(
            base_url=self._proxy_url,
            timeout=self._timeout,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            trust_env=False,
        )

        # Wait for proxy to be ready with retries
        max_retries = 15
        for attempt in range(max_retries):
            try:
                resp = await self._client.get("/health")
                if resp.status_code == 200:
                    logger.info(f"Connected to SQLite Proxy at {self._proxy_url}")
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
            # Lazy initialization: create client on first use if initialize() wasn't called.
            # See initialize() for the trust_env=False rationale (VPN proxy + loopback).
            self._client = httpx.AsyncClient(
                base_url=self._proxy_url,
                timeout=self._timeout,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                trust_env=False,
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
            "txn_id": _current_txn.get(),
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
            "txn_id": _current_txn.get(),
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
            "txn_id": _current_txn.get(),
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
            "txn_id": _current_txn.get(),
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
            "txn_id": _current_txn.get(),
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
            "txn_id": _current_txn.get(),
        })

    # ===== Transaction Support =====

    async def begin_transaction(self) -> None:
        """Begin a transaction on the proxy and capture its token.

        The proxy issues a `txn_id`; every subsequent write on this backend
        carries it so the proxy admits it as the transaction owner's write.
        """
        data = await self._post("/transaction/begin", {})
        _current_txn.set((data or {}).get("txn_id"))

    async def commit(self) -> None:
        """Commit the transaction on the proxy, releasing the token."""
        try:
            await self._post("/transaction/commit", {"txn_id": _current_txn.get()})
        finally:
            _current_txn.set(None)

    async def rollback(self) -> None:
        """Rollback the transaction on the proxy, releasing the token."""
        try:
            await self._post("/transaction/rollback", {"txn_id": _current_txn.get()})
        finally:
            _current_txn.set(None)


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
