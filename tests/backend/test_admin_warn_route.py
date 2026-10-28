"""
@file_name: test_admin_warn_route.py
@author: Bin Liang
@date: 2026-08-19
@description: The admin sensitive-operation warning endpoint —
POST /api/admin/warn-user. Admin-secret gated (same lock as suspend);
writes a fixed generic user_notifications row + a ban_audit(action="warn")
row; idempotent within a dedup window.
"""
from __future__ import annotations

import json as _json

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from narranexus.platform.repository.ban_audit_repository import ACTION_WARN

SECRET = "test-admin-secret-xyz"
UID = "9f3a1c229f3a1c229f3a1c229f3a1c22"  # a real user_id shape
# Opaque, caller-supplied placeholders — this repo asserts on the SHAPE of the
# contract (audit-only, never in the notification), not on any concrete value.
OPAQUE_CATEGORY = "opaque_category"
OPAQUE_ACTOR = "internal_monitor"


def test_action_warn_constant_exists():
    assert ACTION_WARN == "warn"


def _make_app(db_client, monkeypatch, *, secret=SECRET):
    import backend.routes.admin.warn as mod

    async def _ret(v):
        return v

    monkeypatch.setattr(mod, "get_db_client", lambda: _ret(db_client))
    monkeypatch.setattr(mod.settings, "admin_secret_key", secret)
    app = FastAPI()
    app.include_router(mod.router)
    return app


async def _post(app, path, json=None, headers=None):
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
        return await ac.post(path, json=json, headers=headers)


async def _seed_user(db_client, user_id=UID, status="active"):
    await db_client.insert(
        "users", {"user_id": user_id, "user_type": "individual", "status": status}
    )


@pytest.mark.asyncio
async def test_warn_requires_admin_secret(db_client, monkeypatch):
    await _seed_user(db_client)
    app = _make_app(db_client, monkeypatch)
    r = await _post(app, "/api/admin/warn-user", json={"user_id": UID})
    assert r.status_code == 403  # no header -> never open
    r2 = await _post(app, "/api/admin/warn-user", json={"user_id": UID},
                     headers={"X-Admin-Secret": "wrong"})
    assert r2.status_code == 403


@pytest.mark.asyncio
async def test_warn_writes_generic_notification_and_audit(db_client, monkeypatch):
    import backend.routes.admin.warn as mod
    await _seed_user(db_client)
    app = _make_app(db_client, monkeypatch)
    r = await _post(app, "/api/admin/warn-user",
                    json={"user_id": UID, "category": OPAQUE_CATEGORY, "actor": OPAQUE_ACTOR},
                    headers={"X-Admin-Secret": SECRET})
    assert r.status_code == 200
    assert r.json() == {"warned": True, "already": False}

    notes = await db_client.get("user_notifications", {"user_id": UID})
    assert len(notes) == 1
    assert notes[0]["kind"] == "abuse_warning"
    assert notes[0]["severity"] == "warning"
    # Generic wording: payload is always the fixed template, never the caller's category
    payload = _json.loads(notes[0]["payload"])
    assert payload == {"code": "sensitive_operation_warning",
                       "message": mod.SENSITIVE_OP_WARNING}
    # Trust boundary: the opaque category MUST NOT leak into the user notification.
    # Same variable as the request above, so this stays a real assertion.
    assert OPAQUE_CATEGORY not in notes[0]["payload"]

    audits = await db_client.get("ban_audit", {"user_id": UID})
    assert any(a["action"] == ACTION_WARN for a in audits)
    # opaque category goes only to the audit, never to the user message
    assert any((a.get("reason") == OPAQUE_CATEGORY) for a in audits)


@pytest.mark.asyncio
async def test_warn_is_idempotent_within_window(db_client, monkeypatch):
    await _seed_user(db_client)
    app = _make_app(db_client, monkeypatch)
    h = {"X-Admin-Secret": SECRET}
    r1 = await _post(app, "/api/admin/warn-user", json={"user_id": UID}, headers=h)
    r2 = await _post(app, "/api/admin/warn-user", json={"user_id": UID}, headers=h)
    assert r1.json()["already"] is False
    assert r2.json()["already"] is True
    notes = await db_client.get("user_notifications", {"user_id": UID})
    assert len(notes) == 1  # idempotent: the second call writes no duplicate row


@pytest.mark.asyncio
async def test_warn_unknown_user_404(db_client, monkeypatch):
    app = _make_app(db_client, monkeypatch)
    r = await _post(app, "/api/admin/warn-user", json={"user_id": UID},
                    headers={"X-Admin-Secret": SECRET})
    assert r.status_code == 404
