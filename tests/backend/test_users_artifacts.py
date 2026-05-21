"""
@file_name: test_users_artifacts.py
@author: Bin Liang
@date: 2026-05-09
@description: e2e tests for /api/users/{user_id}/artifacts/* under the pointer model.

Covers list, bulk_delete, tenant isolation (skipped_not_owned), and
cloud-mode JWT self-check.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xyz_agent_context.repository.artifact_repository import ArtifactRepository
from xyz_agent_context.schema import Artifact


async def _async_return(value):
    return value


@pytest.fixture
async def setup(db_client, monkeypatch, tmp_path):
    base = tmp_path / "workspaces"
    base.mkdir()
    from xyz_agent_context.settings import settings as sa_settings
    monkeypatch.setattr(sa_settings, "base_working_path", str(base), raising=False)

    from backend.routes.users_artifacts import router as users_router
    import backend.routes.users_artifacts as users_mod
    monkeypatch.setattr(users_mod, "get_db_client", lambda: _async_return(db_client))

    app = FastAPI()
    app.include_router(users_router, prefix="/api/users")

    repo = ArtifactRepository(db_client)
    seeds = [
        ("art_u1", "agent_a", "binliang", "first"),
        ("art_u2", "agent_a", "binliang", "second"),
        ("art_u3", "agent_b", "binliang", "third"),
        ("art_other", "agent_a", "other_user", "not_yours"),
    ]
    # Each seeded artifact is a real folder on disk with one entry file, so
    # `delete_source=true` has real files to remove.
    folder_for_id: dict[str, str] = {}
    for aid, agent, owner, title in seeds:
        workspace = base / f"{agent}_{owner}"
        folder = workspace / aid
        folder.mkdir(parents=True)
        entry = folder / "index.csv"
        entry.write_text("a,b\n1,2\n", encoding="utf-8")
        rel = str(entry.relative_to(base))
        folder_for_id[aid] = str(folder)
        await repo.create(Artifact(
            artifact_id=aid,
            agent_id=agent,
            user_id=owner,
            session_id=None,
            title=title,
            kind="text/csv",
            pinned=True,
            file_path=rel,
            size_bytes=entry.stat().st_size,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        ))

    yield {
        "client": TestClient(app),
        "db": db_client,
        "repo": repo,
        "base": base,
        "folder_for_id": folder_for_id,
    }


def test_list_returns_only_users_artifacts(setup):
    r = setup["client"].get("/api/users/binliang/artifacts")
    assert r.status_code == 200
    ids = {a["artifact_id"] for a in r.json()}
    assert ids == {"art_u1", "art_u2", "art_u3"}


def test_bulk_delete_is_registry_only_workspace_kept(setup):
    """Bulk delete removes DB rows only; the agents' workspace files are
    NEVER touched (no more delete_source)."""
    client = setup["client"]
    r = client.request(
        "DELETE",
        "/api/users/binliang/artifacts",
        json={"artifact_ids": ["art_u1", "art_other"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["deleted"] == 1
    assert body["skipped_not_owned"] == ["art_other"]
    assert "source_deleted" not in body
    # Workspace folder for the deleted row stays on disk.
    assert os.path.exists(setup["folder_for_id"]["art_u1"])


def test_bulk_delete_ignores_stale_delete_source_field(setup):
    """A stale client that sends `delete_source: true` must NOT cause file
    deletion. The endpoint accepts unknown fields silently (Pydantic ignores
    them) and just deletes the registry rows."""
    client = setup["client"]
    r = client.request(
        "DELETE",
        "/api/users/binliang/artifacts",
        json={"artifact_ids": ["art_u1"], "delete_source": True},
    )
    assert r.status_code == 200
    assert os.path.exists(setup["folder_for_id"]["art_u1"])


def test_bulk_delete_empty_body_is_noop(setup):
    r = setup["client"].request(
        "DELETE",
        "/api/users/binliang/artifacts",
        json={"artifact_ids": []},
    )
    assert r.status_code == 200
    assert r.json()["deleted"] == 0


def test_cloud_mode_blocks_other_users(setup, monkeypatch):
    """In cloud mode, a JWT for user A cannot delete user B's artifacts."""
    from starlette.middleware.base import BaseHTTPMiddleware
    from backend.routes.users_artifacts import router as users_router

    class FakeJWTMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.user_id = "attacker"
            return await call_next(request)

    app2 = FastAPI()
    app2.add_middleware(FakeJWTMiddleware)
    app2.include_router(users_router, prefix="/api/users")

    client2 = TestClient(app2)
    r = client2.get("/api/users/binliang/artifacts")
    assert r.status_code == 403

    r = client2.request(
        "DELETE",
        "/api/users/binliang/artifacts",
        json={"artifact_ids": ["art_u1"]},
    )
    assert r.status_code == 403
