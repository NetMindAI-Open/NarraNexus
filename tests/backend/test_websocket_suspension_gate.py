"""
@file_name: test_websocket_suspension_gate.py
@author: Bin Liang
@date: 2026-08-13
@description: The WebSocket account-state gate on /ws/agent/run.

WS is the product's MAIN run path, and the HTTP auth middleware exempts /ws/*
(WebSocket carries its own auth in the first message). So the suspension gate
has to be enforced inside the WS handler too: a suspended user's valid JWT must
NOT be able to start a fresh agent run. These tests drive the handshake with a
TestClient and assert:

- a suspended (banned/blocked/deleted) user is refused with an
  ``account_suspended`` error frame and the socket is closed BEFORE any run
  work begins;
- an active user passes the gate (proven by reaching the circuit-breaker check
  just past it — mocked to skip so no real run starts).

The gate reuses the same shared NON_TRANSACTING_USER_STATUSES set and the same
_account_state reader as the HTTP middleware, so the two cannot drift.
"""
from __future__ import annotations

import contextlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import auth as auth_mod
from backend.auth import create_token
from backend.auth_errors import ACCOUNT_SUSPENDED
import backend.routes.websocket as ws_mod


@pytest.fixture
def force_cloud_mode(monkeypatch):
    # The handler calls the _is_cloud_mode it imported into its own namespace.
    monkeypatch.setattr(ws_mod, "_is_cloud_mode", lambda: True)


@pytest.fixture
def clear_state_cache():
    auth_mod._account_state_cache.clear()
    yield
    auth_mod._account_state_cache.clear()


@pytest.fixture
def wire_db(monkeypatch, db_client):
    """Point _account_state's lazy get_db_client at the in-memory test DB."""
    import narranexus.platform.utils.db.db_factory as db_factory

    async def _ret():
        return db_client

    monkeypatch.setattr(db_factory, "get_db_client", lambda: _ret())
    return db_client


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(ws_mod.router)
    return app


async def _seed(db_client, user_id, status="active"):
    await db_client.insert(
        "users", {"user_id": user_id, "user_type": "individual", "status": status}
    )
    # The run path proves agent ownership right after the account gate
    # (2026-10-28), so the test user must own the agent the message names.
    await db_client.insert(
        "agents", {"agent_id": "agent_1", "agent_name": "a1", "created_by": user_id}
    )


def _first_message(user_id):
    return {
        "agent_id": "agent_1",
        "user_id": user_id,
        "input_content": "hello",
        "token": create_token(user_id, "user"),
    }


@pytest.mark.parametrize("status", ["banned", "blocked", "deleted"])
@pytest.mark.asyncio
async def test_suspended_user_ws_run_refused(
    force_cloud_mode, clear_state_cache, wire_db, status
):
    await _seed(wire_db, "bob", status=status)
    client = TestClient(_build_app())

    with client.websocket_connect("/ws/agent/run") as ws:
        ws.send_json(_first_message("bob"))
        frame = ws.receive_json()

    assert frame["type"] == "error"
    assert frame["error_code"] == ACCOUNT_SUSPENDED


@pytest.mark.asyncio
async def test_active_user_ws_passes_the_gate(
    force_cloud_mode, clear_state_cache, wire_db, monkeypatch
):
    """An active user gets PAST the suspension gate. Proven by reaching the
    circuit-breaker check immediately after it (mocked to skip), so no real
    agent run is started and the first frame is the circuit frame, NOT
    account_suspended."""
    await _seed(wire_db, "alice", status="active")

    import narranexus.platform.agent_framework.loop.circuit_breaker as cb_mod

    async def _skip(_agent_id, db=None):
        return cb_mod.GateVerdict(skip=True, reason="cooling")

    monkeypatch.setattr(cb_mod, "should_skip", _skip)

    client = TestClient(_build_app())

    with client.websocket_connect("/ws/agent/run") as ws:
        ws.send_json(_first_message("alice"))
        frame = ws.receive_json()

    # Not blocked by suspension...
    assert frame.get("error_code") != ACCOUNT_SUSPENDED
    # ...and it reached the circuit-breaker gate just past the account check.
    assert frame["error_type"] == "agent_circuit_open"


@pytest.mark.asyncio
async def test_ws_claims_the_half_open_probe_before_recording_the_run(
    force_cloud_mode, clear_state_cache, wire_db, monkeypatch
):
    """should_skip is a pure read; the probe is claimed by try_begin_probe
    right before the run is recorded. A lost claim answers with the
    "probing" frame and nothing about the run is recorded."""
    await _seed(wire_db, "alice", status="active")

    import narranexus.platform.agent_framework.loop.circuit_breaker as cb_mod

    async def _open(_agent_id, db=None):
        return cb_mod.GateVerdict(skip=False, reason=None)

    async def _lost(_agent_id, db=None, *, prior=None):
        return cb_mod.TurnAdmission(allowed=False, reason="probing")

    recorded = []

    async def _spy_record(**kw):
        recorded.append(kw)

    monkeypatch.setattr(cb_mod, "should_skip", _open)
    monkeypatch.setattr(cb_mod, "try_begin_probe", _lost)
    monkeypatch.setattr(ws_mod, "_record_message_accepted", _spy_record)

    client = TestClient(_build_app())
    with client.websocket_connect("/ws/agent/run") as ws:
        ws.send_json(_first_message("alice"))
        frame = ws.receive_json()

    assert frame["error_type"] == "agent_circuit_open"
    assert frame["cb_reason"] == "probing"
    assert recorded == []


@pytest.mark.asyncio
async def test_ws_hands_back_a_won_probe_when_setup_throws_before_the_run(
    force_cloud_mode, clear_state_cache, wire_db, monkeypatch
):
    """The handler won the half-open probe but threw before a BackgroundRun
    existed to carry the token: the claim is handed back by its token, not
    left PROBING until the grant expires."""
    await _seed(wire_db, "alice", status="active")

    import narranexus.platform.agent_framework.loop.circuit_breaker as cb_mod

    async def _open(_agent_id, db=None):
        return cb_mod.GateVerdict(skip=False, reason=None)

    async def _won(_agent_id, db=None, *, prior=None):
        return cb_mod.TurnAdmission(allowed=True, reason=None, probe_token="tok-ws")

    released = []

    async def _release(agent_id, probe_token, db=None):
        released.append((agent_id, probe_token))
        return True

    async def _boom(**kw):
        raise RuntimeError("setup failed")

    monkeypatch.setattr(cb_mod, "should_skip", _open)
    monkeypatch.setattr(cb_mod, "try_begin_probe", _won)
    monkeypatch.setattr(cb_mod, "release_probe", _release)
    monkeypatch.setattr(ws_mod, "_record_message_accepted", _boom)

    client = TestClient(_build_app())
    with client.websocket_connect("/ws/agent/run") as ws:
        ws.send_json(_first_message("alice"))
        with contextlib.suppress(Exception):
            ws.receive_json()

    assert released == [(_first_message("alice")["agent_id"], "tok-ws")]
