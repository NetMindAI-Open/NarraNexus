"""
@file_name: test_onboarding.py
@author: NarraNexus
@date: 2026-05-21
@description: Tests for the new-user onboarding checklist endpoints.

Mounts the auth router on a fresh FastAPI app, patches get_db_client so
the handlers run against an in-memory SQLite client. Seeds one user via
UserRepository.

Covers:
- GET returns all-false defaults for a user with no metadata
- POST sets a step true and GET reflects it
- write-once-true: a False / None in the request never reverts a flag
- the merge preserves sibling keys in users.metadata
- unknown user -> success=False
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xyz_agent_context.repository.user_repository import UserRepository


async def _async_return(value):
    return value


@pytest.fixture
def client(db_client, monkeypatch):
    import backend.routes.auth as auth_mod

    monkeypatch.setattr(auth_mod, "get_db_client", lambda: _async_return(db_client))

    app = FastAPI()
    app.include_router(auth_mod.router, prefix="/api/auth")
    return TestClient(app)


async def _seed_user(db_client, user_id: str, metadata=None):
    repo = UserRepository(db_client)
    await repo.add_user(
        user_id=user_id,
        user_type="external",
        metadata=metadata,
    )


# ───────────── GET — defaults ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_defaults_all_false(db_client, client):
    await _seed_user(db_client, "u_fresh")
    r = client.get("/api/auth/onboarding", params={"user_id": "u_fresh"})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["progress"] == {
        "first_agent_created": False,
        "template_applied": False,
        "dismissed": False,
    }


@pytest.mark.asyncio
async def test_get_unknown_user_fails(db_client, client):
    r = client.get("/api/auth/onboarding", params={"user_id": "nobody"})
    assert r.status_code == 200
    assert r.json()["success"] is False


# ───────────── POST — set a step ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_sets_step_and_get_reflects(db_client, client):
    await _seed_user(db_client, "u_step")
    r = client.post(
        "/api/auth/onboarding",
        json={"user_id": "u_step", "first_agent_created": True},
    )
    assert r.status_code == 200
    assert r.json()["progress"]["first_agent_created"] is True

    r2 = client.get("/api/auth/onboarding", params={"user_id": "u_step"})
    assert r2.json()["progress"]["first_agent_created"] is True
    # untouched flags stay false
    assert r2.json()["progress"]["template_applied"] is False


# ───────────── write-once-true ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_false_never_reverts_a_completed_step(db_client, client):
    await _seed_user(db_client, "u_once")
    client.post(
        "/api/auth/onboarding",
        json={"user_id": "u_once", "template_applied": True},
    )
    # A later request carrying False must NOT un-complete it.
    r = client.post(
        "/api/auth/onboarding",
        json={"user_id": "u_once", "template_applied": False},
    )
    assert r.json()["progress"]["template_applied"] is True


@pytest.mark.asyncio
async def test_none_leaves_other_flags_intact(db_client, client):
    await _seed_user(db_client, "u_partial")
    client.post(
        "/api/auth/onboarding",
        json={"user_id": "u_partial", "first_agent_created": True},
    )
    # Only dismissed is sent; first_agent_created omitted (None) must persist.
    r = client.post(
        "/api/auth/onboarding",
        json={"user_id": "u_partial", "dismissed": True},
    )
    prog = r.json()["progress"]
    assert prog["first_agent_created"] is True
    assert prog["dismissed"] is True


# ───────────── metadata merge safety ───────────────────────────────────────


@pytest.mark.asyncio
async def test_merge_preserves_sibling_metadata_keys(db_client, client):
    await _seed_user(db_client, "u_sibling", metadata={"keep_me": "value"})
    client.post(
        "/api/auth/onboarding",
        json={"user_id": "u_sibling", "first_agent_created": True},
    )
    repo = UserRepository(db_client)
    user = await repo.get_user("u_sibling")
    assert user.metadata["keep_me"] == "value"
    assert user.metadata["onboarding_progress"]["first_agent_created"] is True
