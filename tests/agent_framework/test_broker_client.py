"""
@file_name: test_broker_client.py
@date: 2026-06-17
@description: Orchestrator-side broker client — gating + URL resolution.
"""
from __future__ import annotations

import httpx
import pytest

from narranexus.platform.agent_framework.loop import broker_client as bc


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
    # The verdict rides along on every ensure, defaulting to the fail-safe
    # side: a caller that has no run context can only ever delay an image
    # roll, never authorise destroying a container in use.
    assert captured["body"] == {"user_id": "alice", "allow_stale_replace": False}


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


@pytest.mark.asyncio
async def test_identity_token_passes_through(monkeypatch):
    monkeypatch.setenv("BROKER_URL", "http://broker:8030")
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={
            "status": "reused",
            "executor_url": "http://nx-exec-a:8020",
            "identity_token": "tok.signed",
        })
    )
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda *a, **k: real_client(transport=transport, **{k2: v for k2, v in k.items() if k2 != "transport"})
    )
    result = await bc.ensure_executor("a")
    assert result.identity_token == "tok.signed"


@pytest.mark.asyncio
async def test_missing_identity_token_reads_none(monkeypatch):
    """An older broker's response simply lacks the field — no lockstep."""
    monkeypatch.setenv("BROKER_URL", "http://broker:8030")
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"status": "reused", "executor_url": "http://nx-exec-a:8020"})
    )
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda *a, **k: real_client(transport=transport, **{k2: v for k2, v in k.items() if k2 != "transport"})
    )
    result = await bc.ensure_executor("a")
    assert result.identity_token is None


@pytest.mark.asyncio
async def test_the_stale_replace_verdict_reaches_the_broker(monkeypatch):
    """A wiring test: the flag is only ever read by the broker, so nothing in
    this process fails if it stops being sent — it just silently reverts to
    "replace unconditionally", which is the 2026-07-31 behaviour."""
    monkeypatch.setenv("BROKER_URL", "http://broker:8030")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        captured["body"] = _json.loads(request.content)
        return httpx.Response(
            200, json={"status": "reused", "executor_url": "http://nx-exec-a:8020"}
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda *a, **k: real_client(
            transport=transport,
            **{k2: v for k2, v in k.items() if k2 != "transport"},
        ),
    )

    await bc.ensure_executor("a", allow_stale_replace=True)
    assert captured["body"]["allow_stale_replace"] is True


@pytest.mark.asyncio
async def test_a_deferred_stale_replacement_is_surfaced(monkeypatch):
    """Deferring is correct, but silence is not: this user runs last deploy's
    executor code for another turn, and a stale executor after a wire-protocol
    change degrades runs without raising anything."""
    from loguru import logger

    monkeypatch.setenv("BROKER_URL", "http://broker:8030")
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={
            "status": "reused",
            "executor_url": "http://nx-exec-a:8020",
            "stale_replace_deferred": True,
        })
    )
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda *a, **k: real_client(
            transport=transport,
            **{k2: v for k2, v in k.items() if k2 != "transport"},
        ),
    )

    # The module logs through loguru, so caplog would see nothing.
    warnings: list[str] = []
    sink = logger.add(lambda m: warnings.append(str(m)), level="WARNING")
    try:
        result = await bc.ensure_executor("a")
    finally:
        logger.remove(sink)

    assert result.url == "http://nx-exec-a:8020"
    assert any("STALE-image" in w for w in warnings)


@pytest.mark.asyncio
async def test_stop_executor_reports_a_busy_refusal(monkeypatch):
    """A refusal is a 200 with `status: busy`, not an error — so nothing
    raises, and the only way the caller can tell is this return value."""
    monkeypatch.setenv("BROKER_URL", "http://broker:8030")
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"status": "busy", "container_id": "c1"})
    )
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda *a, **k: real_client(
            transport=transport,
            **{k2: v for k2, v in k.items() if k2 != "transport"},
        ),
    )
    assert await bc.stop_executor("a") is False


@pytest.mark.asyncio
async def test_stop_executor_reports_a_real_stop(monkeypatch):
    monkeypatch.setenv("BROKER_URL", "http://broker:8030")
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"status": "stopped", "container_id": "c1"})
    )
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda *a, **k: real_client(
            transport=transport,
            **{k2: v for k2, v in k.items() if k2 != "transport"},
        ),
    )
    assert await bc.stop_executor("a") is True


@pytest.mark.asyncio
async def test_stop_executor_is_a_noop_without_a_broker(monkeypatch):
    """True, not False: there is nothing to stop and nothing for the reaper to
    keep holding on to."""
    monkeypatch.delenv("BROKER_URL", raising=False)
    assert await bc.stop_executor("a") is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(204),                              # empty body
        httpx.Response(200, text="<html>gateway</html>"),  # not JSON
        httpx.Response(200, json=["stopped"]),             # JSON, not an object
    ],
    ids=["empty", "not-json", "not-an-object"],
)
async def test_stop_executor_survives_an_unexpected_body(monkeypatch, response):
    """A 2xx whose body is not a JSON object means "it stopped, and the broker
    said so oddly" — never "the call failed". Reporting the opposite would
    make the reaper log a failure for a stop that happened."""
    monkeypatch.setenv("BROKER_URL", "http://broker:8030")
    transport = httpx.MockTransport(lambda req: response)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda *a, **k: real_client(
            transport=transport,
            **{k2: v for k2, v in k.items() if k2 != "transport"},
        ),
    )
    assert await bc.stop_executor("a") is True
