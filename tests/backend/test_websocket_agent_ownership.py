"""
@file_name: test_websocket_agent_ownership.py
@author: Bin Liang
@date: 2026-10-28
@description: /ws/agent/run must prove the caller may use the agent BEFORE a
run starts. The runtime later runs the turn AS the agent's owner
(agent_runtime overrides user_id with created_by), so a stranger who knows an
agent_id would otherwise drive the owner's workspace, tools, channels and
quota. Rule = backend.routes._ownership.assert_visible: owned or public;
otherwise a uniform "agent not found" frame and the socket closes before the
circuit-breaker gate (mocked here to prove ordering). The rule holds in BOTH
run modes: local mode identifies the caller by the ?x_user_id= anchor and is
gated the same way (no local no-op, unlike _ownership's request helpers).
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.routes.websocket as ws_mod
from backend import auth as auth_mod
from backend.auth import create_token


@pytest.fixture
def force_cloud_mode(monkeypatch):
    monkeypatch.setattr(ws_mod, "_is_cloud_mode", lambda: True)


@pytest.fixture
def force_local_mode(monkeypatch):
    monkeypatch.setattr(ws_mod, "_is_cloud_mode", lambda: False)


@pytest.fixture
def clear_state_cache():
    auth_mod._account_state_cache.clear()
    yield
    auth_mod._account_state_cache.clear()


@pytest.fixture
def wire_db(monkeypatch, db_client):
    import narranexus.platform.utils.db.db_factory as db_factory

    async def _ret():
        return db_client
    monkeypatch.setattr(db_factory, "get_db_client", lambda: _ret())
    return db_client


@pytest.fixture
def circuit_marker(monkeypatch):
    """The gate right after ownership; reaching it proves ownership passed."""
    import narranexus.platform.agent_framework.loop.circuit_breaker as cb_mod

    async def _skip(_agent_id, db=None):
        return cb_mod.GateVerdict(skip=True, reason="cooling")
    monkeypatch.setattr(cb_mod, "should_skip", _skip)


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(ws_mod.router)
    return app


async def _seed(db, *, owner="alice", stranger="mallory", public=False):
    for uid in (owner, stranger):
        await db.insert("users", {"user_id": uid, "user_type": "individual", "status": "active"})
    await db.insert("agents", {
        "agent_id": "agent_owned", "agent_name": "x", "created_by": owner, "is_public": 1 if public else 0,
    })


def _msg(user_id, agent_id="agent_owned"):
    return {"agent_id": agent_id, "user_id": user_id, "input_content": "hi", "token": create_token(user_id, "user")}


async def test_stranger_cannot_start_a_run_on_someone_elses_agent(
    force_cloud_mode, clear_state_cache, wire_db, circuit_marker
):
    await _seed(wire_db)
    with TestClient(_app()).websocket_connect("/ws/agent/run") as ws:
        ws.send_json(_msg("mallory"))
        frame = ws.receive_json()
    assert frame["type"] == "error" and frame["error_type"] == "AgentNotFound"


async def test_unknown_agent_id_answers_the_same_not_found(
    force_cloud_mode, clear_state_cache, wire_db, circuit_marker
):
    await _seed(wire_db)
    with TestClient(_app()).websocket_connect("/ws/agent/run") as ws:
        ws.send_json(_msg("mallory", agent_id="agent_nope"))
        frame = ws.receive_json()
    assert frame["error_type"] == "AgentNotFound"


async def test_owner_passes_to_the_circuit_gate(force_cloud_mode, clear_state_cache, wire_db, circuit_marker):
    await _seed(wire_db)
    with TestClient(_app()).websocket_connect("/ws/agent/run") as ws:
        ws.send_json(_msg("alice"))
        frame = ws.receive_json()
    assert frame["error_type"] == "agent_circuit_open"


async def test_public_agent_is_visible_to_a_stranger(force_cloud_mode, clear_state_cache, wire_db, circuit_marker):
    await _seed(wire_db, public=True)
    with TestClient(_app()).websocket_connect("/ws/agent/run") as ws:
        ws.send_json(_msg("mallory"))
        frame = ws.receive_json()
    assert frame["error_type"] == "agent_circuit_open"


# --- local / desktop mode: identity from ?x_user_id=, same visibility rule ---

def _local_msg(user_id, agent_id="agent_owned"):
    return {"agent_id": agent_id, "user_id": user_id, "input_content": "hi"}


async def test_local_mode_owner_passes_to_the_circuit_gate(force_local_mode, wire_db, circuit_marker):
    await _seed(wire_db)
    with TestClient(_app()).websocket_connect("/ws/agent/run?x_user_id=alice") as ws:
        ws.send_json(_local_msg("alice"))
        frame = ws.receive_json()
    assert frame["error_type"] == "agent_circuit_open"


async def test_local_mode_stranger_gets_agent_not_found(force_local_mode, wire_db, circuit_marker):
    await _seed(wire_db)
    with TestClient(_app()).websocket_connect("/ws/agent/run?x_user_id=mallory") as ws:
        ws.send_json(_local_msg("mallory"))
        frame = ws.receive_json()
    assert frame["type"] == "error" and frame["error_type"] == "AgentNotFound"


# --- one home for the visibility rule ---------------------------------------

def test_dashboard_reexports_the_canonical_visibility_helper():
    """The WebHost surface (plugin_sdk_host.agent_visible) still reaches it
    under the dashboard name, and it is the _ownership implementation."""
    from backend.routes import _ownership
    from backend.routes.dashboard import routes as dashboard

    assert dashboard.assert_agent_visible is _ownership.assert_visible


async def test_ws_gate_uses_the_ownership_module(force_cloud_mode, clear_state_cache, wire_db, circuit_marker, monkeypatch):
    from fastapi import HTTPException

    from backend.routes import _ownership

    async def _deny(viewer_id, agent_id):
        raise HTTPException(status_code=404, detail="agent not found")
    monkeypatch.setattr(_ownership, "assert_visible", _deny)
    await _seed(wire_db)
    with TestClient(_app()).websocket_connect("/ws/agent/run") as ws:
        ws.send_json(_msg("alice"))
        frame = ws.receive_json()
    assert frame["error_type"] == "AgentNotFound"
