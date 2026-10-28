"""
@file_name: test_ownership.py
@date: 2026-08-10
@description: Canonical backend agent-ownership helper (_ownership.py).

Locks: owner passes; non-owner is denied (403 / "Permission denied");
unknown agent → 404 / "not found"; local mode (no user_id) skips enforcement.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import backend.routes._ownership as own


class _Req:
    def __init__(self, uid):
        self.state = SimpleNamespace(user_id=uid)


@pytest.fixture(autouse=True)
def _stub_db(monkeypatch):
    async def _db():
        return object()

    monkeypatch.setattr(own, "get_db_client", _db)


def _stub_owner(monkeypatch, owner):
    async def _resolve(self, agent_id):
        return owner

    monkeypatch.setattr(own.AgentRepository, "resolve_owner", _resolve)


@pytest.mark.asyncio
async def test_owner_passes(monkeypatch):
    _stub_owner(monkeypatch, "u1")
    assert await own.check_owned(_Req("u1"), "a") is None
    await own.assert_owned(_Req("u1"), "a")  # does not raise


@pytest.mark.asyncio
async def test_non_owner_denied(monkeypatch):
    _stub_owner(monkeypatch, "u1")
    assert "Permission denied" in (await own.check_owned(_Req("u2"), "a") or "")
    with pytest.raises(HTTPException) as e:
        await own.assert_owned(_Req("u2"), "a")
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_unknown_agent_404(monkeypatch):
    _stub_owner(monkeypatch, "")  # resolve_owner returns "" for unknown
    assert "not found" in (await own.check_owned(_Req("u1"), "a") or "")
    with pytest.raises(HTTPException) as e:
        await own.assert_owned(_Req("u1"), "a")
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_local_mode_skips_enforcement(monkeypatch):
    _stub_owner(monkeypatch, "u1")
    assert await own.check_owned(_Req(None), "a") is None
    await own.assert_owned(_Req(None), "a")  # no raise


@pytest.mark.asyncio
async def test_db_failure_is_503_not_404(monkeypatch):
    """PR #258 review #4: resolve_owner returns None when the LOOKUP failed —
    an infrastructure fault must surface as a server error, never masquerade
    as 'Agent not found' (a db outage would otherwise look like a batch of
    users' agents vanishing, with no 5xx metric to alarm on)."""
    _stub_owner(monkeypatch, None)
    # BOTH surfaces 503 — check_owned's callers wrap returned strings in a
    # 200 payload, which would leave a db outage with zero 5xx to alarm on.
    with pytest.raises(HTTPException) as e:
        await own.check_owned(_Req("u1"), "a")
    assert e.value.status_code == 503
    with pytest.raises(HTTPException) as e:
        await own.assert_owned(_Req("u1"), "a")
    assert e.value.status_code == 503
