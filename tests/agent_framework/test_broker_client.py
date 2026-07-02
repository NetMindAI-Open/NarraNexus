"""
@file_name: test_broker_client.py
@date: 2026-06-17
@description: Orchestrator-side broker client — gating + URL resolution.
"""
from __future__ import annotations

import httpx
import pytest

from xyz_agent_context.agent_framework import broker_client as bc


@pytest.mark.asyncio
async def test_returns_none_when_no_broker_configured(monkeypatch):
    monkeypatch.delenv("BROKER_URL", raising=False)
    assert await bc.ensure_executor("alice") is None


@pytest.mark.asyncio
async def test_resolves_executor_url_from_broker(monkeypatch):
    monkeypatch.setenv("BROKER_URL", "http://broker:8030")

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = httpx.Response(200)  # placeholder
        import json as _json
        captured["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"status": "started", "executor_url": "http://nx-exec-alice:8020"})

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda *a, **k: real_client(transport=transport, **{k2: v for k2, v in k.items() if k2 != "transport"})
    )

    result = await bc.ensure_executor("alice")
    assert result.url == "http://nx-exec-alice:8020"
    assert result.cold_started is True   # status "started" → cold
    assert captured["url"] == "http://broker:8030/executors"
    assert captured["body"] == {"user_id": "alice"}


@pytest.mark.asyncio
async def test_reused_executor_is_not_cold(monkeypatch):
    monkeypatch.setenv("BROKER_URL", "http://broker:8030")
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"status": "reused", "executor_url": "http://nx-exec-a:8020"})
    )
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda *a, **k: real_client(transport=transport, **{k2: v for k2, v in k.items() if k2 != "transport"})
    )
    result = await bc.ensure_executor("a")
    assert result.cold_started is False   # warm reuse → no "waking up" UX


@pytest.mark.asyncio
async def test_raises_when_broker_returns_no_url(monkeypatch):
    monkeypatch.setenv("BROKER_URL", "http://broker:8030")
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"status": "started"}))
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda *a, **k: real_client(transport=transport, **{k2: v for k2, v in k.items() if k2 != "transport"})
    )
    with pytest.raises(RuntimeError):
        await bc.ensure_executor("alice")
