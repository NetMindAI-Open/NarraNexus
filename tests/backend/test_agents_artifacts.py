"""
@file_name: test_agents_artifacts.py
@author: Bin Liang
@date: 2026-05-08
@description: e2e tests for the pointer-model artifact routes.

Covers the agent-scoped JWT-authed router (`/api/agents/*`) plus the
public token-authed raw router (`/api/public/artifacts/*`):
- GET    /{agent_id}/artifacts                            list scope=session|pinned
- POST   /{agent_id}/artifacts/register                   manual register
- GET    /{agent_id}/artifacts/{aid}                      detail
- GET    /{agent_id}/artifacts/{aid}/view-token           mint a token
- PATCH  /{agent_id}/artifacts/{aid}                      { pinned, title }
- DELETE /{agent_id}/artifacts/{aid}?delete_source=…      row only, or row + folder
- GET    /api/public/artifacts/raw/{token}/{file_path}    token-authed serve
- Ownership: JWT user_id != agent.created_by → 403
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
    """Fresh FastAPI mounted with the agent-scoped + public routers, an
    on-disk multi-file artifact in the workspace, and a seeded DB row."""
    base = tmp_path / "workspaces"
    base.mkdir()
    workspace = base / "agent_x_user_y"
    workspace.mkdir()
    root = workspace / "report"
    root.mkdir()
    entry = root / "index.html"
    entry.write_text("<p>hello</p>", encoding="utf-8")
    asset = root / "style.css"
    asset.write_text("body{font:1em monospace}", encoding="utf-8")

    from xyz_agent_context.settings import settings as sa_settings
    monkeypatch.setattr(sa_settings, "base_working_path", str(base), raising=False)
    # Local mode for these tests (no JWT enforcement).
    monkeypatch.setattr(sa_settings, "transcription_hmac_secret", "test-secret", raising=False)
    monkeypatch.setattr(sa_settings, "admin_secret_key", "test-admin", raising=False)

    from backend.routes.agents_artifacts import router as agents_router
    import backend.routes.agents_artifacts as agents_mod
    from backend.routes.artifacts_public import router as public_router
    import backend.routes.artifacts_public as public_mod

    monkeypatch.setattr(agents_mod, "get_db_client", lambda: _async_return(db_client))
    monkeypatch.setattr(public_mod, "get_db_client", lambda: _async_return(db_client))
    # The agents_artifacts module no longer imports `settings` (delete-source
    # rmtree was the only consumer). Only the public router still uses it.
    monkeypatch.setattr(public_mod, "settings", sa_settings)

    # Insert the agent row so manual-register can resolve user_id.
    await db_client.insert("agents", {
        "agent_id": "agent_x",
        "agent_name": "Test agent",
        "created_by": "user_y",
    })

    # Seed an artifact row pointing at the on-disk entry.
    repo = ArtifactRepository(db_client)
    art = Artifact(
        artifact_id="art_99999999",
        agent_id="agent_x",
        user_id="user_y",
        session_id="sess_1",
        title="hello",
        kind="text/html",
        file_path="agent_x_user_y/report/index.html",
        size_bytes=entry.stat().st_size + asset.stat().st_size,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    await repo.create(art)

    app = FastAPI()
    app.include_router(agents_router, prefix="/api/agents")
    app.include_router(public_router, prefix="/api/public/artifacts")

    yield {
        "client": TestClient(app),
        "db": db_client,
        "repo": repo,
        "workspace": workspace,
        "root": root,
        "entry": entry,
        "asset": asset,
        "base": base,
    }


# ─── list / detail ────────────────────────────────────────────────────────────


def test_list_session(setup):
    client = setup["client"]
    r = client.get(
        "/api/agents/agent_x/artifacts",
        params={"scope": "session", "session_id": "sess_1"},
    )
    assert r.status_code == 200
    body = r.json()
    assert any(a["artifact_id"] == "art_99999999" for a in body)


def test_list_session_missing_session_id_returns_400(setup):
    r = setup["client"].get("/api/agents/agent_x/artifacts", params={"scope": "session"})
    assert r.status_code == 400


def test_get_detail_returns_plain_artifact(setup):
    r = setup["client"].get("/api/agents/agent_x/artifacts/art_99999999")
    assert r.status_code == 200
    body = r.json()
    # No `{artifact, versions}` wrapper anymore — plain Artifact.
    assert body["artifact_id"] == "art_99999999"
    assert "file_path" in body
    assert "size_bytes" in body
    assert "versions" not in body


def test_other_agents_cannot_read(setup):
    r = setup["client"].get("/api/agents/AGENT_OTHER/artifacts/art_99999999")
    assert r.status_code == 404


# ─── view-token + public raw serving ──────────────────────────────────────────


def test_view_token_round_trip_serves_entry_html(setup):
    client = setup["client"]
    r = client.get("/api/agents/agent_x/artifacts/art_99999999/view-token")
    assert r.status_code == 200
    body = r.json()
    token = body["token"]
    raw_url = body["raw_url"]
    assert raw_url == f"/api/public/artifacts/raw/{token}/"
    assert body["expires_at"] > 0

    # Token-authed serve of the entry file.
    r2 = client.get(raw_url)
    assert r2.status_code == 200
    assert b"hello" in r2.content
    csp = r2.headers["content-security-policy"]
    # Sibling asset loading needs an explicit host-source, never `'self'`
    # (the iframe is opaque-origin and `'self'` matches nothing there).
    assert "script-src" in csp
    assert "default-src 'none'" in csp


def test_public_raw_serves_sibling_asset(setup):
    client = setup["client"]
    body = client.get("/api/agents/agent_x/artifacts/art_99999999/view-token").json()
    token = body["token"]
    r = client.get(f"/api/public/artifacts/raw/{token}/style.css")
    assert r.status_code == 200
    assert r.content.startswith(b"body")


def test_public_raw_head_allowed_returns_200(setup):
    """HEAD must be routed, not 405. FastAPI GET routes do NOT auto-add HEAD
    (unlike plain Starlette), so the route declares methods=["GET","HEAD"].
    The HtmlRenderer preflight `fetch(url, {method:'HEAD'})` relies on reading
    the real status (200/410) instead of always hitting 405."""
    client = setup["client"]
    token = client.get("/api/agents/agent_x/artifacts/art_99999999/view-token").json()["token"]
    r = client.head(f"/api/public/artifacts/raw/{token}/")
    assert r.status_code == 200
    assert r.content == b""  # HEAD: headers only, no body


def test_public_raw_head_bad_token_is_401_not_405(setup):
    """A bad token on HEAD must reach the handler (401). A 405 here would mean
    HEAD was rejected at routing, before the handler ran."""
    r = setup["client"].head("/api/public/artifacts/raw/not-a-valid-token/x")
    assert r.status_code == 401


def test_public_raw_head_broken_pointer_returns_410(setup):
    """Frontend self-heal triggers on 410. HEAD must surface 410 when the
    on-disk file is gone (live-pointer model), not 405."""
    client = setup["client"]
    token = client.get("/api/agents/agent_x/artifacts/art_99999999/view-token").json()["token"]
    setup["entry"].unlink()  # file gone on disk → 410
    r = client.head(f"/api/public/artifacts/raw/{token}/")
    assert r.status_code == 410


def test_public_raw_path_escape_returns_4xx(setup):
    body = setup["client"].get("/api/agents/agent_x/artifacts/art_99999999/view-token").json()
    token = body["token"]
    # httpx normalises `..` segments at the URL parser, so this lands on
    # `/raw/style.css` (token consumed by the path component). The route
    # pattern then treats `style.css` as the token and fails verification
    # → 401. The real defence against traversal sits on the realpath check
    # inside the handler; this end-to-end probe just asserts the request
    # does not somehow leak content (any 4xx is acceptable).
    r = setup["client"].get(f"/api/public/artifacts/raw/{token}/../style.css")
    assert 400 <= r.status_code < 500


def test_public_raw_invalid_token_returns_401(setup):
    r = setup["client"].get("/api/public/artifacts/raw/not.a.real.token/")
    assert r.status_code in (401, 410)


# ─── register from workspace ──────────────────────────────────────────────────


def test_register_manual_writes_row(setup):
    """The manual-register endpoint goes through the same runner as the MCP
    tool, so the same path-confinement rules apply."""
    workspace = setup["workspace"]
    folder = workspace / "manual"
    folder.mkdir()
    entry = folder / "index.html"
    entry.write_text("<p>manual</p>", encoding="utf-8")

    r = setup["client"].post(
        "/api/agents/agent_x/artifacts/register",
        json={
            "file_path": "manual/index.html",
            "kind": "text/html",
            "title": "Manually registered",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["artifact_id"].startswith("art_")
    assert body["file_path"] == "agent_x_user_y/manual/index.html"


def test_register_manual_accepts_workspace_root_entry(setup):
    """Entry at workspace root is allowed (single-file mode)."""
    flat = setup["workspace"] / "loose.html"
    flat.write_text("<p>flat</p>", encoding="utf-8")
    r = setup["client"].post(
        "/api/agents/agent_x/artifacts/register",
        json={"file_path": "loose.html", "kind": "text/html", "title": "flat"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["file_path"] == "agent_x_user_y/loose.html"
    # Size accounts only for the entry — no recursive workspace sum.
    assert body["size_bytes"] == flat.stat().st_size


def test_public_raw_at_workspace_root_serves_entry_only(setup):
    """Single-file mode at workspace root: entry serves; sub-paths 404
    so we never leak Bootstrap.md / other artifact files as siblings."""
    flat = setup["workspace"] / "loose.html"
    flat.write_text("<p>flat</p>", encoding="utf-8")
    r = setup["client"].post(
        "/api/agents/agent_x/artifacts/register",
        json={"file_path": "loose.html", "kind": "text/html", "title": "flat"},
    )
    aid = r.json()["artifact_id"]

    token_resp = setup["client"].get(
        f"/api/agents/agent_x/artifacts/{aid}/view-token"
    ).json()
    raw_url = token_resp["raw_url"]

    # Entry itself: 200
    r_entry = setup["client"].get(raw_url)
    assert r_entry.status_code == 200
    assert b"flat" in r_entry.content

    # Sibling at workspace root MUST 404 — refuses to expose other files.
    r_sibling = setup["client"].get(f"{raw_url}index.html")  # the seeded report's entry
    assert r_sibling.status_code == 404


# ─── pin / unpin / delete ─────────────────────────────────────────────────────


def test_pin_clears_session_id(setup):
    r = setup["client"].patch(
        "/api/agents/agent_x/artifacts/art_99999999",
        json={"pinned": True},
    )
    assert r.status_code == 200
    assert r.json()["pinned"] is True
    assert r.json()["session_id"] is None


def test_delete_is_registry_only_workspace_files_kept(setup):
    """Deletion never touches workspace files (no more delete_source).
    The user cleans those up via the workspace section if they want."""
    r = setup["client"].delete("/api/agents/agent_x/artifacts/art_99999999")
    assert r.status_code == 200
    body = r.json()
    assert body["deleted"] == "art_99999999"
    assert "source_deleted" not in body  # field is gone

    # Workspace files untouched, including the artifact root folder.
    assert setup["entry"].exists()
    assert setup["asset"].exists()
    assert setup["root"].exists()


def test_delete_source_query_param_is_ignored(setup):
    """Defence-in-depth: even if a stale client sends ?delete_source=true,
    the workspace must NOT be wiped. The endpoint simply ignores it."""
    r = setup["client"].delete(
        "/api/agents/agent_x/artifacts/art_99999999",
        params={"delete_source": "true"},
    )
    assert r.status_code == 200
    assert os.path.exists(setup["root"])
    assert setup["entry"].exists()


# ─── ownership (cloud-mode JWT) ───────────────────────────────────────────────


def test_non_owner_jwt_returns_403(monkeypatch, tmp_path):
    """In cloud mode, JWT user_id != agent.created_by gets 403."""
    import asyncio

    base = tmp_path / "ws"
    base.mkdir()
    from xyz_agent_context.settings import settings as sa_settings
    monkeypatch.setattr(sa_settings, "base_working_path", str(base), raising=False)

    from backend.routes.agents_artifacts import router as agents_router
    import backend.routes.agents_artifacts as agents_mod
    from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend
    from xyz_agent_context.utils.database import AsyncDatabaseClient
    from xyz_agent_context.utils.schema_registry import auto_migrate

    async def _make_db():
        backend = SQLiteBackend(":memory:")
        await backend.initialize()
        await auto_migrate(backend)
        return await AsyncDatabaseClient.create_with_backend(backend)

    db = asyncio.run(_make_db())
    asyncio.run(db.insert("agents", {
        "agent_id": "agent_owned",
        "agent_name": "Test agent",
        "created_by": "owner_user",
    }))

    monkeypatch.setattr(agents_mod, "get_db_client", lambda: _async_return(db))
    # agents_artifacts no longer imports `settings`; nothing to patch there.

    app = FastAPI()
    from starlette.middleware.base import BaseHTTPMiddleware

    class FakeJWTMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.user_id = "other_user"
            return await call_next(request)

    app.add_middleware(FakeJWTMiddleware)
    app.include_router(agents_router, prefix="/api/agents")

    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/api/agents/agent_owned/artifacts", params={"scope": "pinned"})
    assert r.status_code == 403


def test_unpin_agent_scoped_artifact_returns_400(setup):
    """Agent-created artifacts (session_id=None, pinned=True) have no
    original_session_id to restore — must refuse the unpin attempt rather
    than silently dropping the artifact into limbo."""
    import asyncio
    from datetime import datetime as _dt

    art = Artifact(
        artifact_id="art_agent999",
        agent_id="agent_x", user_id="user_y",
        session_id=None, title="agent-emitted", kind="text/csv",
        pinned=True,
        file_path="agent_x_user_y/some/data.csv", size_bytes=1,
        created_at=_dt.now(timezone.utc),
        updated_at=_dt.now(timezone.utc),
    )
    asyncio.run(setup["repo"].create(art))

    r = setup["client"].patch(
        "/api/agents/agent_x/artifacts/art_agent999",
        json={"pinned": False},
    )
    assert r.status_code == 400
    assert "agent-scoped" in r.text or "DELETE" in r.text
