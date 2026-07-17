"""
@file_name: test_mcp_repository_headers.py
@author:
@date: 2026-07-15
@description: Tests for MCP custom-header support in MCPRepository and
validate_mcp_sse_connection.

Custom headers (e.g. Authorization bearer tokens for authenticated SSE
MCP endpoints) round-trip through the mcp_urls table as a JSON column and
are merged into the validation request. Uses real in-memory SQLite (via
conftest db_client fixture).
"""
from __future__ import annotations

import pytest

from xyz_agent_context.repository.mcp_repository import (
    MCPRepository,
    validate_mcp_sse_connection,
)

HEADERS = {"Authorization": "Bearer secret-token-1234567890"}


@pytest.mark.asyncio
async def test_add_mcp_roundtrips_headers(db_client):
    repo = MCPRepository(db_client)
    await repo.add_mcp(
        agent_id="agent_1",
        user_id="user_1",
        mcp_id="mcp_headers1",
        name="web3",
        url="http://frps.example.com:6027/sse",
        headers=HEADERS,
    )
    mcp = await repo.get_mcp("mcp_headers1")
    assert mcp is not None
    assert mcp.headers == HEADERS


@pytest.mark.asyncio
async def test_add_mcp_without_headers_stays_none(db_client):
    repo = MCPRepository(db_client)
    await repo.add_mcp(
        agent_id="agent_1",
        user_id="user_1",
        mcp_id="mcp_nohdr1",
        name="plain",
        url="http://localhost:7801/sse",
    )
    mcp = await repo.get_mcp("mcp_nohdr1")
    assert mcp is not None
    assert mcp.headers is None


@pytest.mark.asyncio
async def test_update_mcp_replaces_headers(db_client):
    repo = MCPRepository(db_client)
    await repo.add_mcp(
        agent_id="agent_1",
        user_id="user_1",
        mcp_id="mcp_upd1",
        name="web3",
        url="http://frps.example.com:6027/sse",
        headers=HEADERS,
    )
    await repo.update_mcp("mcp_upd1", {"headers": {"X-Api-Key": "k2"}})
    mcp = await repo.get_mcp("mcp_upd1")
    assert mcp.headers == {"X-Api-Key": "k2"}


@pytest.mark.asyncio
async def test_update_mcp_clears_headers_with_none(db_client):
    repo = MCPRepository(db_client)
    await repo.add_mcp(
        agent_id="agent_1",
        user_id="user_1",
        mcp_id="mcp_clr1",
        name="web3",
        url="http://frps.example.com:6027/sse",
        headers=HEADERS,
    )
    await repo.update_mcp("mcp_clr1", {"headers": None})
    mcp = await repo.get_mcp("mcp_clr1")
    assert mcp.headers is None


@pytest.mark.asyncio
async def test_get_mcps_by_agent_user_carries_headers(db_client):
    repo = MCPRepository(db_client)
    await repo.add_mcp(
        agent_id="agent_1",
        user_id="user_1",
        mcp_id="mcp_list1",
        name="web3",
        url="http://frps.example.com:6027/sse",
        headers=HEADERS,
    )
    mcps = await repo.get_mcps_by_agent_user("agent_1", "user_1")
    assert len(mcps) == 1
    assert mcps[0].headers == HEADERS


@pytest.mark.asyncio
async def test_update_mcp_does_not_mutate_caller_dict(db_client):
    repo = MCPRepository(db_client)
    await repo.add_mcp(
        agent_id="agent_1",
        user_id="user_1",
        mcp_id="mcp_mut1",
        name="web3",
        url="http://frps.example.com:6027/sse",
    )
    updates = {"headers": {"X-Api-Key": "k"}}
    await repo.update_mcp("mcp_mut1", updates)
    assert updates["headers"] == {"X-Api-Key": "k"}  # still a dict, not JSON text


@pytest.mark.asyncio
async def test_validate_connection_sends_custom_headers(monkeypatch):
    """validate_mcp_sse_connection must merge custom headers into the
    streaming request (on top of the SSE Accept/Cache-Control baseline)."""
    import httpx

    captured: dict = {}

    class _FakeStreamResponse:
        status_code = 200
        headers = {"content-type": "text/event-stream"}

        async def aiter_bytes(self):
            yield b"data: ok\n\n"

    class _FakeStreamCtx:
        async def __aenter__(self):
            return _FakeStreamResponse()

        async def __aexit__(self, *args):
            return False

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def stream(self, method, url, headers=None):
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = headers
            return _FakeStreamCtx()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    ok, error = await validate_mcp_sse_connection(
        "http://frps.example.com:6027/sse", headers=HEADERS
    )
    assert ok is True and error is None
    assert captured["headers"]["Authorization"] == HEADERS["Authorization"]
    assert captured["headers"]["Accept"] == "text/event-stream"


@pytest.mark.asyncio
async def test_validate_connection_anonymous_without_headers(monkeypatch):
    """No custom headers → request carries only the SSE baseline headers."""
    import httpx

    captured: dict = {}

    class _FakeStreamResponse:
        status_code = 200
        headers = {"content-type": "text/event-stream"}

        async def aiter_bytes(self):
            yield b"data: ok\n\n"

    class _FakeStreamCtx:
        async def __aenter__(self):
            return _FakeStreamResponse()

        async def __aexit__(self, *args):
            return False

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def stream(self, method, url, headers=None):
            captured["headers"] = headers
            return _FakeStreamCtx()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    ok, _ = await validate_mcp_sse_connection("http://localhost:7801/sse")
    assert ok is True
    assert set(captured["headers"].keys()) == {"Accept", "Cache-Control"}


@pytest.mark.asyncio
async def test_validate_connection_baseline_accept_wins_over_user_header(monkeypatch):
    """A user-supplied Accept must not evict the SSE baseline — validation
    asserts a text/event-stream response, so overriding Accept would fail a
    healthy endpoint."""
    import httpx

    captured: dict = {}

    class _FakeStreamResponse:
        status_code = 200
        headers = {"content-type": "text/event-stream"}

        async def aiter_bytes(self):
            yield b"data: ok\n\n"

    class _FakeStreamCtx:
        async def __aenter__(self):
            return _FakeStreamResponse()

        async def __aexit__(self, *args):
            return False

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def stream(self, method, url, headers=None):
            captured["headers"] = headers
            return _FakeStreamCtx()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    ok, _ = await validate_mcp_sse_connection(
        "http://localhost:7801/sse",
        headers={"Accept": "application/json", "Authorization": "Bearer t"},
    )
    assert ok is True
    assert captured["headers"]["Accept"] == "text/event-stream"
    assert captured["headers"]["Authorization"] == "Bearer t"
