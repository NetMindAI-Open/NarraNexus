"""
@file_name: test_artifact_content_put.py
@date: 2026-08-19
@description: e2e tests for the user-edit content endpoint (spec A §3.1):

  PUT /api/agents/{agent_id}/artifacts/{artifact_id}/content
      body {content, base_hash}

- 200: file replaced, refreshed Artifact returned (new content_hash).
- 409: stale base_hash → {current_hash} so the editor can re-base.
- 400/413/404/410 map straight from the ArtifactError hierarchy.
- view-token raw route stays read-only — no PUT there.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from narranexus.platform.repository.artifact_repository import ArtifactRepository
from narranexus.platform.schema import Artifact
from narranexus.platform.utils.workspace_paths import agent_workspace_relpath

WS_REL = agent_workspace_relpath("agent_x", "user_y")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _async_return(value):
    return value


@pytest.fixture
async def setup(db_client, monkeypatch, tmp_path):
    base = tmp_path / "workspaces"
    base.mkdir()
    workspace = base / WS_REL
    workspace.mkdir(parents=True)
    entry = workspace / "notes.md"
    entry.write_text("# old\n", encoding="utf-8")

    from narranexus.platform.settings import settings as sa_settings
    monkeypatch.setattr(sa_settings, "base_working_path", str(base), raising=False)

    from backend.routes.agents.artifacts import router as agents_router
    import backend.routes.agents.artifacts as agents_mod
    monkeypatch.setattr(agents_mod, "get_db_client", lambda: _async_return(db_client))

    await db_client.insert("agents", {
        "agent_id": "agent_x",
        "agent_name": "Test agent",
        "created_by": "user_y",
    })

    repo = ArtifactRepository(db_client)
    await repo.create(Artifact(
        artifact_id="art_put00001",
        agent_id="agent_x",
        user_id="user_y",
        session_id="sess_1",
        title="notes",
        kind="text/markdown",
        file_path=f"{WS_REL}/notes.md",
        size_bytes=entry.stat().st_size,
        content_hash=_sha("# old\n"),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    ))

    app = FastAPI()
    app.include_router(agents_router, prefix="/api/agents")
    yield {"client": TestClient(app), "entry": entry, "repo": repo}


def test_put_content_saves_and_returns_refreshed_artifact(setup):
    client = setup["client"]
    r = client.put(
        "/api/agents/agent_x/artifacts/art_put00001/content",
        json={"content": "# new\n", "base_hash": _sha("# old\n")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["artifact_id"] == "art_put00001"
    assert body["content_hash"] == _sha("# new\n")
    assert setup["entry"].read_text(encoding="utf-8") == "# new\n"


def test_put_content_stale_hash_is_409_with_current_hash(setup):
    client = setup["client"]
    r = client.put(
        "/api/agents/agent_x/artifacts/art_put00001/content",
        json={"content": "# clobber\n", "base_hash": _sha("stale")},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["current_hash"] == _sha("# old\n")
    assert setup["entry"].read_text(encoding="utf-8") == "# old\n"


def test_put_content_unknown_artifact_is_404(setup):
    r = setup["client"].put(
        "/api/agents/agent_x/artifacts/art_missing0/content",
        json={"content": "x", "base_hash": "y"},
    )
    assert r.status_code == 404


def test_put_content_gone_entry_is_410(setup):
    setup["entry"].unlink()
    r = setup["client"].put(
        "/api/agents/agent_x/artifacts/art_put00001/content",
        json={"content": "# new\n", "base_hash": _sha("# old\n")},
    )
    assert r.status_code == 410


def test_put_content_lying_content_length_streamed_cap_fires(setup):
    """review #334 r3 I1: the header can lie (declare 1KB, stream 30MB) —
    the in-handler streamed accumulation must 413 without the body ever
    being framework-buffered (no Pydantic body field on this route)."""
    big = b'{"content":"' + b"x" * (30 * 1024 * 1024) + b'","base_hash":"h"}'

    def gen():
        for i in range(0, len(big), 1024 * 1024):
            yield big[i:i + 1024 * 1024]

    r = setup["client"].put(
        "/api/agents/agent_x/artifacts/art_put00001/content",
        content=gen(),  # chunked: no Content-Length at all
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413
    assert setup["entry"].read_text(encoding="utf-8") == "# old\n"


def test_put_content_malformed_body_is_422(setup):
    r = setup["client"].put(
        "/api/agents/agent_x/artifacts/art_put00001/content",
        content=b"not json at all",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 422


def test_body_size_middleware_rejects_declared_oversize_before_route():
    """The ONLY layer that runs before FastAPI buffers a body is HTTP
    middleware — pin that the declared-length gate fires there with the
    route never entered."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.middleware.body_size import body_size_middleware

    hits = []
    app = FastAPI()
    app.middleware("http")(body_size_middleware)

    @app.put("/api/agents/a/artifacts/b/content")
    async def _route():  # pragma: no cover - must not run
        hits.append(1)
        return {}

    client = TestClient(app)
    r = client.put(
        "/api/agents/a/artifacts/b/content",
        content=b"tiny",
        headers={"Content-Length": str(28 * 1024 * 1024), "Content-Type": "application/json"},
    )
    assert r.status_code == 413
    assert hits == []
