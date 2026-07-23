"""
@file_name: test_agents_url_artifacts.py
@author: Bin Liang
@date: 2026-07-22
@description: e2e route tests for URL-tab endpoints (open URL + embed-mode).

The probe + SSRF gate are monkeypatched so these tests are network-free and
deterministic; the focus is the HTTP surface (creation, ownership, override,
validation).
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xyz_agent_context.schema.artifact_schema import EmbedVerdict, UrlArtifactDoc
from xyz_agent_context.utils.workspace_paths import agent_workspace_relpath

WS_REL = agent_workspace_relpath("agent_x", "user_y")


async def _async_return(value):
    return value


@pytest.fixture
async def client(db_client, monkeypatch, tmp_path):
    base = tmp_path / "workspaces"
    base.mkdir()
    (base / WS_REL).mkdir(parents=True)

    from xyz_agent_context.settings import settings as sa_settings
    monkeypatch.setattr(sa_settings, "base_working_path", str(base), raising=False)

    # Network-free probe + SSRF gate.
    async def fake_probe(url, *, our_scheme, resolver=None, client=None):
        rec = "stream" if "blocked" in url else "iframe"
        return EmbedVerdict(recommended=rec, reason="no-blocking-headers", probe_status="ok")

    async def fake_assert(url, *, resolver=None):
        if "internal" in url:
            from xyz_agent_context.utils.url_safety import UnsafeUrlError
            raise UnsafeUrlError("blocked")
        return ["93.184.216.34"]

    import xyz_agent_context.artifact._artifact_impl.url_artifact as ua
    monkeypatch.setattr(ua, "probe_url", fake_probe)
    monkeypatch.setattr(ua, "assert_public_http_url", fake_assert)

    from backend.routes.agents_artifacts import router as agents_router
    import backend.routes.agents_artifacts as agents_mod
    monkeypatch.setattr(agents_mod, "get_db_client", lambda: _async_return(db_client))

    await db_client.insert("agents", {
        "agent_id": "agent_x", "agent_name": "T", "created_by": "user_y",
    })

    app = FastAPI()
    app.include_router(agents_router, prefix="/api/agents")
    yield TestClient(app), base


def test_open_url_creates_iframe_tab(client):
    c, base = client
    r = c.post("/api/agents/agent_x/artifacts/url", json={"url": "https://ok.example/", "title": "OK"})
    assert r.status_code == 200, r.text
    art = r.json()
    assert art["kind"] == "application/x-url"
    assert art["title"] == "OK"
    doc = UrlArtifactDoc.model_validate_json((base / art["file_path"]).read_text())
    assert doc.embed.recommended == "iframe"


def test_open_url_probe_says_stream(client):
    c, base = client
    r = c.post("/api/agents/agent_x/artifacts/url", json={"url": "https://blocked.example/"})
    assert r.status_code == 200
    doc = UrlArtifactDoc.model_validate_json((base / r.json()["file_path"]).read_text())
    assert doc.embed.recommended == "stream"


def test_open_url_rejects_internal(client):
    c, _ = client
    r = c.post("/api/agents/agent_x/artifacts/url", json={"url": "http://internal/admin"})
    assert r.status_code == 400


def test_embed_mode_override_round_trip(client):
    c, base = client
    art = c.post("/api/agents/agent_x/artifacts/url", json={"url": "https://ok.example/"}).json()
    aid = art["artifact_id"]

    r = c.post(f"/api/agents/agent_x/artifacts/{aid}/embed-mode", json={"mode": "stream"})
    assert r.status_code == 200
    doc = UrlArtifactDoc.model_validate_json((base / r.json()["file_path"]).read_text())
    assert doc.embed.user_override == "stream"

    r2 = c.post(f"/api/agents/agent_x/artifacts/{aid}/embed-mode", json={"mode": None})
    assert r2.status_code == 200
    doc2 = UrlArtifactDoc.model_validate_json((base / r2.json()["file_path"]).read_text())
    assert doc2.embed.user_override is None


def test_embed_mode_invalid_value_422(client):
    # mode is typed EmbedMode on the request model → pydantic rejects (422).
    c, _ = client
    art = c.post("/api/agents/agent_x/artifacts/url", json={"url": "https://ok.example/"}).json()
    r = c.post(f"/api/agents/agent_x/artifacts/{art['artifact_id']}/embed-mode", json={"mode": "banana"})
    assert r.status_code == 422


def test_embed_mode_missing_artifact_404(client):
    c, _ = client
    r = c.post("/api/agents/agent_x/artifacts/art_nope/embed-mode", json={"mode": "stream"})
    assert r.status_code == 404
