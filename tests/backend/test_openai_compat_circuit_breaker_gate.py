"""
@file_name: test_openai_compat_circuit_breaker_gate.py
@author:
@date: 2026-09-10
@description: /v1/chat/completions is a turn-starting entry point and takes
the same circuit-breaker gate as every other one (#394 review I5).

Before, it started BackgroundRuns with no gate at all — against a paused
agent — and since BackgroundRun feeds the breaker, an unclaimed turn here
could decide another turn's half-open probe. Now: should_skip, then
try_begin_probe at the last moment before the run, refusals answer a
readable OpenAI-shape 503, and a won claim rides the run (probe_token).
Real breaker (sqlite); the run itself is faked.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport
from backend.run_lifecycle import RunTasks

import backend.routes.openai_compat as compat_mod
import narranexus.platform.agent_framework.loop.circuit_breaker as cb
from narranexus.platform.repository.agent_circuit_breaker_repository import (
    AgentCircuitBreakerRepository,
)
from narranexus.platform.schema import CbStatus, PausedReason
from narranexus.platform.utils.timezone import utc_now


class _FakeBroadcaster:
    def subscribe(self, session_id):
        async def _events():
            return
            yield  # pragma: no cover — an empty stream

        return _events()

    def unsubscribe(self, session_id):
        pass


class _FakeBackgroundRun:
    instances: list = []

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.task = None
        self.ready_event = asyncio.Event()
        self.ready_event.set()
        self.broadcaster = _FakeBroadcaster()
        _FakeBackgroundRun.instances.append(self)

    async def drive(self, **kwargs):
        pass


@pytest.fixture
def app(monkeypatch, db_client):
    _FakeBackgroundRun.instances = []

    async def fake_creator(agent_id):
        return "user_1"

    async def fake_db():
        return db_client

    monkeypatch.setattr(compat_mod, "_resolve_agent_creator", fake_creator)
    monkeypatch.setattr(compat_mod, "get_db_client", fake_db)
    monkeypatch.setattr(cb, "get_db_client", fake_db)
    monkeypatch.setattr(compat_mod, "BackgroundRun", _FakeBackgroundRun)

    application = FastAPI()

    @application.middleware("http")
    async def _authed(request: Request, call_next):
        request.state.manyfold_authed = True
        return await call_next(request)

    application.include_router(compat_mod.router)
    application.state.active_runs = {}
    application.state.run_tasks = RunTasks()
    return application


async def _post(app, agent_id="agent_cb"):
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.post("/v1/chat/completions", json={
            "model": agent_id,
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
        })


async def test_returned_http_request_leaves_run_owned_until_drain(app, monkeypatch):
    from unittest.mock import AsyncMock

    work = asyncio.Event()

    async def drive(self, **kwargs):
        await work.wait()

    monkeypatch.setattr(_FakeBackgroundRun, "drive", drive)
    response = await _post(app)
    assert response.status_code == 200
    cleanup = AsyncMock()
    closing = asyncio.create_task(app.state.run_tasks.close(cleanup))
    await asyncio.sleep(0)
    cleanup.assert_not_awaited()
    assert not closing.done()
    assert not _FakeBackgroundRun.instances[0].task.done()
    work.set()
    await closing
    cleanup.assert_awaited_once()


async def test_shutdown_refusal_returns_probe_claim(app, db_client):
    from unittest.mock import AsyncMock

    await _pause(db_client, window_open=True)
    await app.state.run_tasks.close(AsyncMock())
    with pytest.raises(RuntimeError, match="shutting down"):
        await _post(app)
    row = await AgentCircuitBreakerRepository(db_client).get("agent_cb")
    assert row.cb_status == CbStatus.PAUSED


async def _pause(db, *, window_open: bool) -> None:
    await AgentCircuitBreakerRepository(db).upsert_state("agent_cb", {
        "cb_status": CbStatus.PAUSED.value,
        "paused_reason": PausedReason.AUTH.value,
        "failure_category": "auth",
        "consecutive_failure_count": 3,
        "cooldown_until": utc_now() + (
            timedelta(seconds=-1) if window_open else timedelta(minutes=5)
        ),
    })


async def test_paused_agent_is_refused_with_a_readable_error(app, db_client):
    await _pause(db_client, window_open=False)
    resp = await _post(app)
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["type"] == "agent_circuit_open"
    assert body["error"]["code"] == "paused:auth"
    assert "authentication" in body["error"]["message"].lower()
    assert body["model"] == "agent_cb"
    assert _FakeBackgroundRun.instances == []


async def test_open_window_claims_the_probe_and_hands_it_to_the_run(app, db_client):
    await _pause(db_client, window_open=True)
    resp = await _post(app)
    assert resp.status_code == 200
    row = await AgentCircuitBreakerRepository(db_client).get("agent_cb")
    assert row.cb_status == CbStatus.PROBING.value
    (run,) = _FakeBackgroundRun.instances
    assert run.init_kwargs["probe_token"] == row.probe_token

    # A second request inside the grant loses to the live probe.
    resp = await _post(app)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "probing"
    assert len(_FakeBackgroundRun.instances) == 1


async def test_healthy_agent_runs_without_a_claim(app, db_client):
    resp = await _post(app)
    assert resp.status_code == 200
    (run,) = _FakeBackgroundRun.instances
    assert run.init_kwargs["probe_token"] is None


async def test_a_claim_whose_run_never_starts_is_handed_back(app, db_client, monkeypatch):
    """#394 second review M-1: setup throws between the claim and the run's
    task — the probe goes back to PAUSED by its token instead of blocking
    every entry until the grant expires."""
    class _Boom(_FakeBackgroundRun):
        def __init__(self, **kwargs):
            raise RuntimeError("run setup failed")

    monkeypatch.setattr(compat_mod, "BackgroundRun", _Boom)
    await _pause(db_client, window_open=True)
    with pytest.raises(RuntimeError):
        await _post(app)
    row = await AgentCircuitBreakerRepository(db_client).get("agent_cb")
    assert row.cb_status == CbStatus.PAUSED.value
    assert row.probe_token is None
