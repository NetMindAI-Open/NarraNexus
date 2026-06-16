"""
@file_name: test_admin_migration_route.py
@author: NarraNexus
@date: 2026-06-12
@description: POST /api/admin/migrate-identity — admin-secret-gated single-user
identity migration (legacy user_id -> NetMind userSystemCode). Wraps the shared
identity_migration kernel so a batch script can call it per user (stack stopped)
and ad-hoc rebinds reuse the same path.

Covers:
- no / wrong X-Admin-Secret -> 403 (high-risk endpoint, never open)
- valid secret -> rekeys the user's data to the hex, returns stats
- power_email / power_display_name update the target users row
- rejects a malformed (non-32-char) userSystemCode
"""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

SECRET = "test-admin-secret-xyz"
OLD = "binliang"
HEX = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"


def _make_app(db_client, monkeypatch, tmp_path, *, secret=SECRET):
    import backend.routes.admin_migration as mod

    async def _ret(v):
        return v

    monkeypatch.setattr(mod, "get_db_client", lambda: _ret(db_client))
    monkeypatch.setattr(mod.settings, "admin_secret_key", secret)
    monkeypatch.setattr(mod.settings, "base_working_path", str(tmp_path))

    app = FastAPI()
    app.include_router(mod.router)
    return app


async def _post(app, json=None, headers=None):
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
        return await ac.post("/api/admin/migrate-identity", json=json, headers=headers)


async def _seed_legacy_user(db_client):
    await db_client.insert("users", {"user_id": OLD, "user_type": "individual"})
    await db_client.insert(
        "agents", {"agent_id": "ag1", "agent_name": "A", "created_by": OLD}
    )


@pytest.mark.asyncio
async def test_missing_secret_is_rejected(db_client, monkeypatch, tmp_path):
    await _seed_legacy_user(db_client)
    app = _make_app(db_client, monkeypatch, tmp_path)

    resp = await _post(app, json={"from_user_id": OLD, "to_power_hex": HEX})

    assert resp.status_code == 403
    # data untouched
    assert (await db_client.get_one("users", {"user_id": OLD})) is not None
    assert (await db_client.get_one("users", {"user_id": HEX})) is None


@pytest.mark.asyncio
async def test_wrong_secret_is_rejected(db_client, monkeypatch, tmp_path):
    await _seed_legacy_user(db_client)
    app = _make_app(db_client, monkeypatch, tmp_path)

    resp = await _post(
        app,
        json={"from_user_id": OLD, "to_power_hex": HEX},
        headers={"X-Admin-Secret": "nope"},
    )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_valid_secret_migrates_user(db_client, monkeypatch, tmp_path):
    await _seed_legacy_user(db_client)
    app = _make_app(db_client, monkeypatch, tmp_path)

    resp = await _post(
        app,
        json={"from_user_id": OLD, "to_power_hex": HEX},
        headers={"X-Admin-Secret": SECRET},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["users_migrated"] == 1
    assert body["merged"] is False
    # identity rekeyed
    assert (await db_client.get_one("users", {"user_id": OLD})) is None
    assert (await db_client.get_one("users", {"user_id": HEX})) is not None
    assert (await db_client.get_one("agents", {"agent_id": "ag1"}))[
        "created_by"
    ] == HEX


@pytest.mark.asyncio
async def test_updates_target_display_name_and_email(db_client, monkeypatch, tmp_path):
    await _seed_legacy_user(db_client)
    app = _make_app(db_client, monkeypatch, tmp_path)

    resp = await _post(
        app,
        json={
            "from_user_id": OLD, "to_power_hex": HEX,
            "power_email": "bin@netmind.ai", "power_display_name": "Bin Liang",
        },
        headers={"X-Admin-Secret": SECRET},
    )

    assert resp.status_code == 200
    target = await db_client.get_one("users", {"user_id": HEX})
    assert target["display_name"] == "Bin Liang"
    assert target["email"] == "bin@netmind.ai"


@pytest.mark.asyncio
async def test_rejects_malformed_hex(db_client, monkeypatch, tmp_path):
    await _seed_legacy_user(db_client)
    app = _make_app(db_client, monkeypatch, tmp_path)

    resp = await _post(
        app,
        json={"from_user_id": OLD, "to_power_hex": "too-short"},
        headers={"X-Admin-Secret": SECRET},
    )

    assert resp.status_code == 400
