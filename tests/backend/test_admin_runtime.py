"""
@file_name: test_admin_runtime.py
@author:
@date: 2026-06-18
@description: Tests for GET /admin/runtime/status + AgentAdmissionController.snapshot().

Two groups:
  1. snapshot() unit tests — verify shape, defaults, and that queue_depth
     reflects in-flight waiters.
  2. Route integration tests — assert 200 + expected JSON keys via ASGI
     transport; no real DB row reads are exercised (audit counts come back
     as {}).
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from xyz_agent_context.agent_runtime.admission import (
    AgentAdmissionController,
    reset_admission_controller_for_test,
)


# ---------------------------------------------------------------------------
# Part A: snapshot() unit tests
# ---------------------------------------------------------------------------

def _ctrl(
    max_users: int = 20,
    max_loops_per_user: int = 5,
    max_loops_global: int = 50,
    min_free_mem_mb: int = 6144,
) -> AgentAdmissionController:
    return AgentAdmissionController(
        max_users=max_users,
        max_loops_per_user=max_loops_per_user,
        max_loops_global=max_loops_global,
        min_free_mem_mb=min_free_mem_mb,
    )


def test_snapshot_keys_present():
    c = _ctrl()
    snap = c.snapshot()
    expected_keys = {
        "active_users",
        "active_loops",
        "queue_depth",
        "max_users",
        "max_loops_per_user",
        "max_loops_global",
        "min_free_mem_mb",
        "free_mem_mb",
        "per_user_loops",
        "enabled",
    }
    assert expected_keys == set(snap.keys())


def test_snapshot_values_at_rest():
    c = _ctrl()
    snap = c.snapshot()
    assert snap["max_loops_global"] == 50
    assert snap["max_users"] == 20
    assert snap["max_loops_per_user"] == 5
    assert snap["min_free_mem_mb"] == 6144
    assert snap["active_loops"] == 0
    assert snap["queue_depth"] == 0
    assert snap["active_users"] == 0
    assert snap["per_user_loops"] == {}
    assert snap["enabled"] is True


@pytest.mark.asyncio
async def test_snapshot_active_loops_updates():
    c = _ctrl()
    tok = await c.acquire("u1")
    snap = c.snapshot()
    assert snap["active_loops"] == 1
    assert snap["active_users"] == 1
    assert snap["per_user_loops"] == {"u1": 1}
    await c.release(tok)
    snap2 = c.snapshot()
    assert snap2["active_loops"] == 0


@pytest.mark.asyncio
async def test_snapshot_queue_depth_increments_while_waiting():
    """queue_depth rises when a waiter blocks, falls after release."""
    c = AgentAdmissionController(
        max_users=None,
        max_loops_per_user=1,
        max_loops_global=None,
        min_free_mem_mb=0,
    )
    tok = await c.acquire("u")
    # Now spin up a second acquire that will block (per-user cap=1)
    waiting_task = asyncio.create_task(c.acquire("u"))
    await asyncio.sleep(0.05)  # let the task reach wait_for
    assert c.snapshot()["queue_depth"] == 1
    await c.release(tok)
    await asyncio.sleep(0.05)  # let waiter wake
    assert waiting_task.done()
    assert c.snapshot()["queue_depth"] == 0
    await c.release(await waiting_task)


def test_snapshot_disabled_controller():
    c = AgentAdmissionController(
        max_users=None,
        max_loops_per_user=None,
        max_loops_global=None,
        min_free_mem_mb=0,
    )
    snap = c.snapshot()
    assert snap["enabled"] is False
    assert snap["max_loops_global"] is None


# ---------------------------------------------------------------------------
# Part B: route integration tests
# ---------------------------------------------------------------------------

def _make_app(db_client, monkeypatch, ctrl: AgentAdmissionController):
    """Build a minimal FastAPI app with the admin_runtime router wired up.

    Monkeypatches:
    - admin_runtime.get_db_client → returns the test db_client
    - admin_runtime.get_admission_controller → returns ctrl
    """
    import backend.routes.admin_runtime as mod

    async def _fake_db():
        return db_client

    monkeypatch.setattr(mod, "get_db_client", _fake_db)
    monkeypatch.setattr(mod, "get_admission_controller", lambda: ctrl)

    app = FastAPI()
    app.include_router(mod.router)
    return app


async def _get_status(app) -> httpx.Response:
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        return await ac.get("/api/admin/runtime/status")


@pytest.mark.asyncio
async def test_status_returns_200(db_client, monkeypatch):
    ctrl = _ctrl()
    reset_admission_controller_for_test(ctrl)
    try:
        app = _make_app(db_client, monkeypatch, ctrl)
        resp = await _get_status(app)
        assert resp.status_code == 200
    finally:
        reset_admission_controller_for_test(None)


@pytest.mark.asyncio
async def test_status_has_required_top_level_keys(db_client, monkeypatch):
    ctrl = _ctrl()
    reset_admission_controller_for_test(ctrl)
    try:
        app = _make_app(db_client, monkeypatch, ctrl)
        resp = await _get_status(app)
        body = resp.json()
        assert "admission" in body
        assert "executors" in body
        assert "audit_counts" in body
    finally:
        reset_admission_controller_for_test(None)


@pytest.mark.asyncio
async def test_status_admission_contains_max_loops_global(db_client, monkeypatch):
    ctrl = _ctrl(max_loops_global=77)
    reset_admission_controller_for_test(ctrl)
    try:
        app = _make_app(db_client, monkeypatch, ctrl)
        resp = await _get_status(app)
        body = resp.json()
        assert body["admission"]["max_loops_global"] == 77
        assert body["admission"]["active_loops"] == 0
    finally:
        reset_admission_controller_for_test(None)


@pytest.mark.asyncio
async def test_status_audit_counts_is_dict(db_client, monkeypatch):
    ctrl = _ctrl()
    reset_admission_controller_for_test(ctrl)
    try:
        app = _make_app(db_client, monkeypatch, ctrl)
        resp = await _get_status(app)
        body = resp.json()
        # empty DB → empty or valid dict
        assert isinstance(body["audit_counts"], dict)
    finally:
        reset_admission_controller_for_test(None)


@pytest.mark.asyncio
async def test_status_executors_present(db_client, monkeypatch):
    """executors key is present (broker not configured → unavailable indicator)."""
    ctrl = _ctrl()
    reset_admission_controller_for_test(ctrl)
    try:
        app = _make_app(db_client, monkeypatch, ctrl)
        resp = await _get_status(app)
        body = resp.json()
        # Brokers is not configured in tests so returns the unavailable dict
        assert "executors" in body
    finally:
        reset_admission_controller_for_test(None)


@pytest.mark.asyncio
async def test_status_resilient_when_broker_raises(db_client, monkeypatch):
    """If broker query raises, endpoint still returns 200 with executors fallback."""
    import backend.routes.admin_runtime as mod

    ctrl = _ctrl()
    reset_admission_controller_for_test(ctrl)
    try:
        async def _fake_db():
            return db_client

        async def _bad_broker_url() -> str | None:
            raise RuntimeError("broker down")

        monkeypatch.setattr(mod, "get_db_client", _fake_db)
        monkeypatch.setattr(mod, "get_admission_controller", lambda: ctrl)
        # Patch the broker_url check so it raises
        monkeypatch.setattr(mod, "_get_executor_list", _bad_broker_url)

        app = FastAPI()
        app.include_router(mod.router)
        resp = await _get_status(app)
        assert resp.status_code == 200
        body = resp.json()
        assert "executors" in body
    finally:
        reset_admission_controller_for_test(None)
