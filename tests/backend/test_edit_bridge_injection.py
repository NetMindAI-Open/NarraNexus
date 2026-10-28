"""
@file_name: test_edit_bridge_injection.py
@date: 2026-08-19
@description: The public raw route injects the per-element edit bridge script
into an HTML artifact's ENTRY document when (and only when) the viewer asks
for it with ?edit_bridge=1 (spec A §3.3):

- entry html + edit_bridge=1  → bridge <script> present, marker string in body
- entry html, no param        → served bytes untouched
- sibling asset               → never injected (param ignored)
- non-html entry              → never injected (param ignored)
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from narranexus.platform.repository.artifact_repository import ArtifactRepository
from narranexus.platform.schema import Artifact
from narranexus.platform.utils.workspace_paths import agent_workspace_relpath

WS_REL = agent_workspace_relpath("agent_x", "user_y")

HTML = "<html><body><h1>Title</h1></body></html>"


async def _async_return(value):
    return value


@pytest.fixture
async def setup(db_client, monkeypatch, tmp_path):
    base = tmp_path / "workspaces"
    base.mkdir()
    workspace = base / WS_REL
    workspace.mkdir(parents=True)
    root = workspace / "page"
    root.mkdir()
    (root / "index.html").write_text(HTML, encoding="utf-8")
    (root / "extra.html").write_text(HTML, encoding="utf-8")
    (workspace / "notes.md").write_text("# hi\n", encoding="utf-8")

    from narranexus.platform.settings import settings as sa_settings
    monkeypatch.setattr(sa_settings, "base_working_path", str(base), raising=False)
    monkeypatch.setattr(sa_settings, "transcription_hmac_secret", "test-secret", raising=False)

    from backend.routes.agents.artifacts import router as agents_router
    import backend.routes.agents.artifacts as agents_mod
    from backend.routes.artifacts.public import router as public_router
    import backend.routes.artifacts.public as public_mod
    monkeypatch.setattr(agents_mod, "get_db_client", lambda: _async_return(db_client))
    monkeypatch.setattr(public_mod, "get_db_client", lambda: _async_return(db_client))

    await db_client.insert("agents", {
        "agent_id": "agent_x", "agent_name": "t", "created_by": "user_y",
    })
    repo = ArtifactRepository(db_client)
    now = datetime.now(timezone.utc)
    await repo.create(Artifact(
        artifact_id="art_htmlpage", agent_id="agent_x", user_id="user_y",
        session_id="s", title="page", kind="text/html",
        file_path=f"{WS_REL}/page/index.html", size_bytes=1,
        created_at=now, updated_at=now,
    ))
    await repo.create(Artifact(
        artifact_id="art_mdnote00", agent_id="agent_x", user_id="user_y",
        session_id="s", title="notes", kind="text/markdown",
        file_path=f"{WS_REL}/notes.md", size_bytes=1,
        created_at=now, updated_at=now,
    ))

    app = FastAPI()
    app.include_router(agents_router, prefix="/api/agents")
    app.include_router(public_router, prefix="/api/public/artifacts")
    client = TestClient(app)

    def token_for(aid: str) -> str:
        return client.get(f"/api/agents/agent_x/artifacts/{aid}/view-token").json()["token"]

    yield {"client": client, "token_for": token_for}


def test_entry_html_with_param_gets_the_bridge(setup):
    client, token_for = setup["client"], setup["token_for"]
    t = token_for("art_htmlpage")
    r = client.get(f"/api/public/artifacts/raw/{t}/?edit_bridge=1")
    assert r.status_code == 200
    assert "narra-edit-bridge" in r.text
    assert "<h1>Title</h1>" in r.text  # original content intact


def test_entry_html_without_param_is_untouched(setup):
    client, token_for = setup["client"], setup["token_for"]
    t = token_for("art_htmlpage")
    r = client.get(f"/api/public/artifacts/raw/{t}/")
    assert r.status_code == 200
    assert r.text == HTML


def test_sibling_asset_never_injected(setup):
    client, token_for = setup["client"], setup["token_for"]
    t = token_for("art_htmlpage")
    r = client.get(f"/api/public/artifacts/raw/{t}/extra.html?edit_bridge=1")
    assert r.status_code == 200
    assert "narra-edit-bridge" not in r.text


def test_non_html_entry_never_injected(setup):
    client, token_for = setup["client"], setup["token_for"]
    t = token_for("art_mdnote00")
    r = client.get(f"/api/public/artifacts/raw/{t}/?edit_bridge=1")
    assert r.status_code == 200
    assert "narra-edit-bridge" not in r.text


def test_non_utf8_entry_serves_bytes_untouched_without_bridge(setup, tmp_path):
    """review #334 I4: a GBK/latin-1 html must NOT be decoded with
    errors="replace" and re-encoded — that turns every non-UTF-8 byte into
    U+FFFD and the edit pipeline would write the corruption back. The bridge
    branch degrades to the plain FileResponse: viewable, not editable."""
    gbk_bytes = "<html><body><h1>GBK quarterly report - \u5b63\u5ea6\u62a5\u544a</h1></body></html>".encode("gbk")
    base = tmp_path / "workspaces"
    root = base / WS_REL / "gbkpage"
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_bytes(gbk_bytes)

    client, token_for = setup["client"], setup["token_for"]
    # Register through the manual-register endpoint — same validation path.
    r = client.post(
        "/api/agents/agent_x/artifacts/register",
        json={"file_path": "gbkpage/index.html", "kind": "text/html", "title": "gbk"},
    )
    assert r.status_code == 200, r.text
    aid = r.json()["artifact_id"]
    t = token_for(aid)
    resp = client.get(f"/api/public/artifacts/raw/{t}/?edit_bridge=1")
    assert resp.status_code == 200
    assert b"narra-edit-bridge" not in resp.content
    assert resp.content == gbk_bytes  # byte-identical, no U+FFFD
