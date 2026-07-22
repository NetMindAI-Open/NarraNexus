"""
@file_name: test_sqlite_proxy_txn.py
@author: NarraNexus
@date: 2026-07-20
@description: Regression tests for cross-process-safe transactions in the
SQLite proxy (sqlite_proxy_server.py + db_backend_sqlite_proxy.py).

Guards two historical defects in the proxy's transaction handling:

- P1 (silent data corruption): before the fix, a single global
  `_in_transaction` flag on the shared connection meant a NON-owner
  process's write was folded into whatever transaction happened to be
  open, and got committed/rolled-back with it. The fix gates every write
  behind a per-transaction `txn_id`: only the holder's writes pass; every
  other write blocks until the transaction ends. `test_foreign_write_is_blocked`
  proves the block (a folded write would have executed immediately).

- P4 (stuck-flag poisoning): a client that died mid-transaction left the
  flag set forever, silently disabling auto-commit process-wide. The fix
  adds a watchdog that force-rolls-back an abandoned transaction past its
  deadline. `test_watchdog_reaps_abandoned_txn` drives the reaper directly.

The proxy FastAPI app is driven in-process via httpx ASGITransport; the
backend + transaction primitives are wired up manually so no real network
or lifespan is needed.
"""
from __future__ import annotations

import asyncio
import contextvars
import time

import httpx
import pytest
import pytest_asyncio

from xyz_agent_context.utils import sqlite_proxy_server as proxy
from xyz_agent_context.utils import db_backend_sqlite_proxy as proxy_client_mod
from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend
from xyz_agent_context.utils.db_backend_sqlite_proxy import SQLiteProxyBackend


@pytest_asyncio.fixture
async def proxy_client(tmp_path):
    """Wire the proxy module to a fresh on-disk backend + reset txn state.

    Yields an httpx.AsyncClient bound to the proxy ASGI app. The backend
    uses a real file (not :memory:) so it survives the single connection
    the proxy holds; a tiny `t` table is created for the tests to write to.
    """
    backend = SQLiteBackend(str(tmp_path / "proxy_test.db"))
    await backend.initialize()
    await backend.execute_write(
        "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)"
    )

    proxy._backend = backend
    proxy._reset_txn_state()

    transport = httpx.ASGITransport(app=proxy.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://proxy") as client:
        yield client

    await backend.close()
    proxy._backend = None


async def _begin(client) -> str:
    resp = await client.post("/transaction/begin", json={})
    body = resp.json()
    assert body["success"], body
    return body["data"]["txn_id"]


async def _count_rows(client) -> int:
    resp = await client.post("/get", json={"table": "t"})
    body = resp.json()
    assert body["success"], body
    return len(body["data"])


@pytest.mark.asyncio
async def test_owner_transaction_commits_atomically(proxy_client):
    txn_id = await _begin(proxy_client)
    for name in ("a", "b"):
        resp = await proxy_client.post(
            "/insert", json={"table": "t", "data": {"name": name}, "txn_id": txn_id}
        )
        assert resp.json()["success"]

    resp = await proxy_client.post("/transaction/commit", json={"txn_id": txn_id})
    assert resp.json()["success"]
    assert await _count_rows(proxy_client) == 2


@pytest.mark.asyncio
async def test_owner_transaction_rollback_discards(proxy_client):
    txn_id = await _begin(proxy_client)
    resp = await proxy_client.post(
        "/insert", json={"table": "t", "data": {"name": "c"}, "txn_id": txn_id}
    )
    assert resp.json()["success"]

    resp = await proxy_client.post("/transaction/rollback", json={"txn_id": txn_id})
    assert resp.json()["success"]
    assert await _count_rows(proxy_client) == 0


@pytest.mark.asyncio
async def test_foreign_write_is_blocked_then_proceeds(proxy_client):
    """P1 regression: a write without the active txn_id must WAIT for the
    transaction to end — it must not execute (fold) while the txn is open."""
    txn_id = await _begin(proxy_client)
    # Deterministic: the slot is claimed and the gate is closed.
    assert proxy._active_txn == txn_id
    assert proxy._txn_done is not None and not proxy._txn_done.is_set()

    # A different "process" writes with no token — must block.
    foreign = asyncio.ensure_future(
        proxy_client.post("/insert", json={"table": "t", "data": {"name": "foreign"}})
    )
    done, pending = await asyncio.wait({foreign}, timeout=0.3)
    assert foreign in pending, "foreign write should be blocked during an open txn"

    # Ending the transaction releases the blocked write.
    resp = await proxy_client.post("/transaction/commit", json={"txn_id": txn_id})
    assert resp.json()["success"]

    resp = await foreign
    assert resp.json()["success"]
    assert await _count_rows(proxy_client) == 1


@pytest.mark.asyncio
async def test_watchdog_reaps_abandoned_txn(proxy_client):
    """P4 regression: an abandoned transaction past its deadline is
    force-rolled-back by the reaper, unblocking subsequent writes."""
    txn_id = await _begin(proxy_client)
    assert proxy._active_txn == txn_id

    # Force the deadline into the past and run one reaper pass.
    proxy._txn_deadline = time.monotonic() - 1.0
    await proxy._reap_expired_txn()
    assert proxy._active_txn is None

    # A normal (token-less) write now succeeds immediately.
    resp = await proxy_client.post("/insert", json={"table": "t", "data": {"name": "after"}})
    assert resp.json()["success"]
    assert await _count_rows(proxy_client) == 1


@pytest.mark.asyncio
async def test_client_threads_txn_id(monkeypatch):
    """Client side: begin sets the context token, writes carry it, commit clears it."""
    backend = SQLiteProxyBackend("http://unused")
    calls: list[tuple[str, dict]] = []

    async def fake_post(path, payload):
        calls.append((path, dict(payload)))
        if path == "/transaction/begin":
            return {"txn_id": "ptxn_test"}
        return 0

    monkeypatch.setattr(backend, "_post", fake_post)

    await backend.begin_transaction()
    assert proxy_client_mod._current_txn.get() == "ptxn_test"

    await backend.insert("t", {"name": "x"})
    insert_path, insert_payload = calls[-1]
    assert insert_path == "/insert"
    assert insert_payload["txn_id"] == "ptxn_test"

    await backend.commit()
    commit_path, commit_payload = calls[-1]
    assert commit_path == "/transaction/commit"
    assert commit_payload["txn_id"] == "ptxn_test"
    assert proxy_client_mod._current_txn.get() is None


@pytest.mark.asyncio
async def test_txn_token_is_context_scoped(monkeypatch):
    """Issue-2 regression: an independent request (a separate asyncio Task with
    its own context) must NOT inherit an open transaction's token — otherwise
    its write would be admitted as the holder's and folded into the transaction.
    The token lives in a ContextVar, not on the shared per-loop backend instance."""
    backend = SQLiteProxyBackend("http://unused")
    seen: list[tuple[str, dict]] = []

    async def fake_post(path, payload):
        seen.append((path, dict(payload)))
        return {"txn_id": "ptxn_ctx"} if path == "/transaction/begin" else 0

    monkeypatch.setattr(backend, "_post", fake_post)

    # Snapshot a context from BEFORE any transaction — this is what an
    # independent, concurrently-arriving request task would run under.
    independent_ctx = contextvars.copy_context()

    await backend.begin_transaction()  # sets the token in THIS task's context
    assert proxy_client_mod._current_txn.get() == "ptxn_ctx"

    async def independent_write():
        await backend.insert("t", {"name": "other"})

    task = asyncio.create_task(independent_write(), context=independent_ctx)
    await task

    other = next(p for path, p in seen if path == "/insert")
    assert other["txn_id"] is None, "independent task must not inherit the txn token"

    await backend.rollback()
    assert proxy_client_mod._current_txn.get() is None


@pytest.mark.asyncio
async def test_raw_execute_write_is_gated(proxy_client):
    """Issue-1 regression: a write routed through /execute (raw UPDATE with
    fetch=True, as ModulePoller issues) must block during an open transaction
    rather than fold into it — /execute is gated like the write endpoints."""
    txn_id = await _begin(proxy_client)

    foreign = asyncio.ensure_future(
        proxy_client.post("/execute", json={"query": "UPDATE t SET name='z' WHERE 1=1"})
    )
    done, pending = await asyncio.wait({foreign}, timeout=0.3)
    assert foreign in pending, "raw /execute write should be blocked during an open txn"

    resp = await proxy_client.post("/transaction/commit", json={"txn_id": txn_id})
    assert resp.json()["success"]

    resp = await foreign
    assert resp.json()["success"]
