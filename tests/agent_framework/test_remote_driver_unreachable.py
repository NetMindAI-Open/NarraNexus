"""
@file_name: test_remote_driver_unreachable.py
@date: 2026-07-22
@description: The executor transport boundaries convert a connection failure
into the typed ExecutorUnreachableError, so step_3 can surface an actionable
``infra_transient`` error instead of a bare aiohttp/httpx exception escaping
the pipeline (issue ②). Covers both boundaries:
  - RemoteAgentLoopDriver.agent_loop — aiohttp ClientConnectorError at the
    :8020 connect
  - broker_client.ensure_executor / wait_until_ready — httpx transport error /
    a container that never boots
"""
from __future__ import annotations

import aiohttp
import httpx
import pytest
from aiohttp.client_reqrep import ConnectionKey

from xyz_agent_context.agent_framework.executor_errors import (
    ExecutorUnreachableError,
)
from xyz_agent_context.agent_framework.remote_agent_loop_driver import (
    RemoteAgentLoopDriver,
)
from xyz_agent_context.agent_framework import broker_client


def _connector_error(host: str, port: int) -> aiohttp.ClientConnectorError:
    key = ConnectionKey(host, port, False, True, None, None, None)
    return aiohttp.ClientConnectorError(key, OSError(61, "Connection refused"))


class _FakeSession:
    """Async-context session whose .post() fails to connect."""

    def __init__(self, host: str, port: int):
        self._host, self._port = host, port

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, *args, **kwargs):
        raise _connector_error(self._host, self._port)


@pytest.mark.asyncio
async def test_agent_loop_connect_error_becomes_unreachable(monkeypatch):
    driver = RemoteAgentLoopDriver("claude", "/tmp/wp", "http://nx-exec-abc:8020")

    def _fake_client_session(*args, **kwargs):
        return _FakeSession("nx-exec-abc", 8020)

    monkeypatch.setattr(aiohttp, "ClientSession", _fake_client_session)

    with pytest.raises(ExecutorUnreachableError) as ei:
        async for _ in driver.agent_loop(
            messages=[{"role": "user", "content": "hi"}],
            mcp_servers={},
        ):
            pass
    assert ei.value.target == "http://nx-exec-abc:8020/agent-loop"


class _FakeStreamResp:
    """A response that streams `chunks`, then raises `raise_at_end` from
    iter_any — models the executor container dying mid-run."""

    def __init__(self, chunks, raise_at_end):
        self._chunks = chunks
        self._raise = raise_at_end

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        return None

    @property
    def content(self):
        return self

    async def iter_any(self):
        for c in self._chunks:
            yield c
        if self._raise is not None:
            raise self._raise


class _FakeStreamSession:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, *args, **kwargs):
        return self._resp


@pytest.mark.asyncio
async def test_agent_loop_mid_run_disconnect_becomes_unreachable(monkeypatch):
    # Stream one valid event, then the connection drops mid-run — the executor
    # was killed. This previously fell through as a generic exception and got
    # masked by the fabricating fallback; now it is surfaced as unreachable.
    resp = _FakeStreamResp(
        chunks=[b'{"event": {"type": "ping"}}\n'],
        raise_at_end=aiohttp.ServerDisconnectedError("connection lost"),
    )
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: _FakeStreamSession(resp))

    driver = RemoteAgentLoopDriver("claude", "/tmp/wp", "http://nx-exec-abc:8020")
    seen = []
    with pytest.raises(ExecutorUnreachableError):
        async for ev in driver.agent_loop(
            messages=[{"role": "user", "content": "hi"}], mcp_servers={}
        ):
            seen.append(ev)
    assert seen == [{"type": "ping"}]  # the pre-disconnect event still came through


class _FakeHttpxClient:
    """Async-context httpx client whose .post() raises a transport error."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, *args, **kwargs):
        raise httpx.ConnectError("Connection refused")


@pytest.mark.asyncio
async def test_ensure_executor_transport_error_becomes_unreachable(monkeypatch):
    monkeypatch.setenv("BROKER_URL", "http://broker:9000")
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda *a, **k: _FakeHttpxClient()
    )

    with pytest.raises(ExecutorUnreachableError) as ei:
        await broker_client.ensure_executor("user1")
    assert ei.value.target == "http://broker:9000"


@pytest.mark.asyncio
async def test_wait_until_ready_timeout_becomes_unreachable(monkeypatch):
    # Container never answers /health → typed unreachable, not bare RuntimeError.
    async def _never_healthy(_health_url: str) -> bool:
        return False

    monkeypatch.setattr(broker_client, "_executor_healthy", _never_healthy)

    with pytest.raises(ExecutorUnreachableError):
        await broker_client.wait_until_ready(
            "http://nx-exec-abc:8020", timeout=0.05, interval=0.01
        )
