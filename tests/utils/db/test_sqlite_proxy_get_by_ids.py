"""
@file_name: test_sqlite_proxy_get_by_ids.py
@date: 2026-08-07
@description: The proxy backend must accept and FORWARD every `get_by_ids`
argument the client can send.

Why this file exists
====================
`AsyncDatabaseClient.get_by_ids` forwards its arguments to whichever backend
`db_factory` selected, so a parameter added to only some of the three
implementations is a `TypeError` on **every** call through the others — the
default value does not save you, because it is still passed by keyword.

That shipped: `fields` was added to the MySQL and SQLite backends and to the
client's delegation, but not to the proxy backend. Cloud (MySQL) stayed green
while `run.sh` and the desktop DMG — both of which set `SQLITE_PROXY_URL` and
therefore route through the proxy — broke on every `get_by_ids`, i.e. every
`BaseRepository` batch load, the inbox, the teams page and chat-context
assembly. Binding rule #7 in one sentence: change one run mode, check the other.

The existing proxy test file covers transactions only, so nothing caught it.
Two tests here, and they must stay a pair:

- `test_accepts_fields` fails on the TypeError (the crash)
- `test_forwards_fields` fails if the argument is accepted but dropped at the
  HTTP boundary (the silent downgrade to `SELECT *`, which saves nothing and
  would never be noticed)
"""
from __future__ import annotations

import httpx
import pytest
import pytest_asyncio

from narranexus.platform.utils.db import sqlite_proxy_server as proxy
from narranexus.platform.utils.db.db_backend_sqlite import SQLiteBackend
from narranexus.platform.utils.db.db_backend_sqlite_proxy import SQLiteProxyBackend

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def proxy_backend(tmp_path):
    """A SQLiteProxyBackend whose HTTP calls land on the proxy app in-process."""
    backend = SQLiteBackend(str(tmp_path / "proxy_fields.db"))
    await backend.initialize()
    await backend.execute_write(
        "CREATE TABLE snap (text_hash TEXT PRIMARY KEY, text TEXT, note TEXT)"
    )
    for i in range(3):
        await backend.execute_write(
            "INSERT INTO snap (text_hash, text, note) VALUES (?, ?, ?)",
            (f"h{i}", f"body-{i}", f"note-{i}"),
        )

    proxy._backend = backend
    proxy._reset_txn_state()

    client = SQLiteProxyBackend("http://proxy")
    client._client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=proxy.app), base_url="http://proxy"
    )
    yield client

    await client._client.aclose()
    await backend.close()
    proxy._backend = None


async def test_accepts_fields(proxy_backend):
    """The signature must match the client's call — including `fields=None`."""
    rows = await proxy_backend.get_by_ids("snap", "text_hash", ["h0", "h1"], fields=None)
    assert [r["text_hash"] for r in rows if r] == ["h0", "h1"]


async def test_forwards_fields_across_the_http_boundary(proxy_backend):
    """Accepting `fields` is not enough — it has to reach the SQL.

    Swallowing it client-side stops the TypeError while silently returning
    `SELECT *`, so the projection saves nothing on exactly the path (desktop /
    run.sh) where nobody would think to check.
    """
    rows = await proxy_backend.get_by_ids(
        "snap", "text_hash", ["h0", "h2"], fields=["text_hash"]
    )
    present = [r for r in rows if r]
    assert [r["text_hash"] for r in present] == ["h0", "h2"]
    for r in present:
        assert set(r) == {"text_hash"}, (
            f"projection was dropped somewhere between the client and the SQL: "
            f"got columns {sorted(r)}"
        )


async def test_missing_ids_still_pad_with_none(proxy_backend):
    """Order preservation must survive the projection.

    `get_by_ids` pads misses with None to keep input order; the caller-visible
    contract must not change just because fewer columns were requested.
    """
    rows = await proxy_backend.get_by_ids(
        "snap", "text_hash", ["h0", "nope", "h1"], fields=["text_hash"]
    )
    assert [r["text_hash"] if r else None for r in rows] == ["h0", None, "h1"]
