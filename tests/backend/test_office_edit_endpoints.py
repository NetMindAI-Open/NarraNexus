"""
@file_name: test_office_edit_endpoints.py
@date: 2026-08-19
@description: The two backend halves of office T1 direct editing (spec B §3):

1. POST passthrough on the office-watch proxy — path-allowlisted
   (api/send | api/batch | api/selection), token+port gated, body capped.
   Everything else stays GET-only.
2. POST /{agent_id}/artifacts/{aid}/office-edit-commit — after the watch
   server applied a user's op, this turns it into a commit point: hash
   recompute from disk, history action="user_edited", staged event.
   Idempotent on an unchanged hash.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import pathlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.routes.office_watch.proxy as owp
from backend.routes.office_watch._token import mint
from narranexus.platform.repository.artifact_repository import ArtifactRepository
from narranexus.platform.schema import Artifact
from narranexus.platform.utils.workspace_paths import agent_workspace_relpath

WS_REL = agent_workspace_relpath("agent_x", "user_y")


async def _async_return(value):
    return value


# ── proxy POST allowlist ─────────────────────────────────────────────────────


@pytest.fixture
def proxy_client(monkeypatch):
    monkeypatch.setattr(
        "narranexus.platform.settings.settings.transcription_hmac_secret",
        "test-secret",
        raising=False,
    )
    calls: list = []

    async def fake_post_upstream(user_id, port, path, query, body, content_type):
        calls.append({"user_id": user_id, "port": port, "path": path, "body": body})
        return 200, "application/json", b'{"success": true}'

    monkeypatch.setattr(owp, "_post_upstream", fake_post_upstream)
    app = FastAPI()
    app.include_router(owp.public_router, prefix="/api/public")
    return {"client": TestClient(app), "calls": calls}


def test_post_selection_forwards(proxy_client):
    """The one public POST left: the watch page's own selection report."""
    token = mint(user_id="user_y", port=26320)
    r = proxy_client["client"].post(
        f"/api/public/office-watch-proxy/{token}/26320/api/selection",
        json={"paths": ["/body/p[1]"]},
    )
    assert r.status_code == 200
    assert proxy_client["calls"][0]["path"] == "api/selection"


def test_post_edit_verbs_are_no_longer_public(proxy_client):
    """review #334 I14: the 2h view token must never grant writes — api/send
    and api/batch left the public allowlist for the session-authed
    /office-watch/edit endpoint."""
    token = mint(user_id="user_y", port=26320)
    for path in ("api/batch", "api/send"):
        r = proxy_client["client"].post(
            f"/api/public/office-watch-proxy/{token}/26320/{path}",
            json=[],
        )
        assert r.status_code == 405, path
    assert proxy_client["calls"] == []


def test_post_disallowed_path_is_405(proxy_client):
    token = mint(user_id="user_y", port=26320)
    r = proxy_client["client"].post(
        f"/api/public/office-watch-proxy/{token}/26320/api/shutdown",
        json={},
    )
    assert r.status_code == 405
    assert proxy_client["calls"] == []


def test_authed_edit_endpoint_forwards_batch(monkeypatch, proxy_client):
    """/office-watch/edit: session-authed, ensures the watch, forwards to
    the watch server's /api/batch."""
    async def fake_lookup(request, artifact_id):
        return ("user_y", "agent_x", "/abs/deck.pptx", "deck.pptx")

    monkeypatch.setattr(owp, "_lookup_office_file", fake_lookup)
    monkeypatch.setattr(owp, "is_cloud_mode", lambda: False)
    monkeypatch.setattr(owp, "ensure_watch", lambda *a: 26320)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(owp.router, prefix="/api")
    client = TestClient(app)
    r = client.post(
        "/api/office-watch/edit?artifact_id=art_deck0001",
        json=[{"command": "remove", "path": "/slide[2]"}],
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"success": True}
    assert proxy_client["calls"][-1]["path"] == "api/batch"
    assert b'"remove"' in proxy_client["calls"][-1]["body"]


def test_post_port_mismatch_is_403(proxy_client):
    token = mint(user_id="user_y", port=26320)
    r = proxy_client["client"].post(
        f"/api/public/office-watch-proxy/{token}/26321/api/send",
        json={},
    )
    assert r.status_code == 403
    assert proxy_client["calls"] == []


def test_post_oversize_body_is_413(proxy_client):
    token = mint(user_id="user_y", port=26320)
    r = proxy_client["client"].post(
        f"/api/public/office-watch-proxy/{token}/26320/api/selection",
        content=b"x" * (64 * 1024 + 1),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413
    assert proxy_client["calls"] == []


# ── office-edit-commit ───────────────────────────────────────────────────────


@pytest.fixture
async def commit_env(db_client, monkeypatch, tmp_path):
    base = tmp_path / "workspaces"
    (base / WS_REL).mkdir(parents=True)
    from narranexus.platform.settings import settings as sa
    monkeypatch.setattr(sa, "base_working_path", str(base), raising=False)

    from backend.routes.agents.artifacts import router as agents_router
    import backend.routes.agents.artifacts as agents_mod
    monkeypatch.setattr(agents_mod, "get_db_client", lambda: _async_return(db_client))
    await db_client.insert("agents", {
        "agent_id": "agent_x", "agent_name": "t", "created_by": "user_y",
    })

    entry = base / WS_REL / "deck.pptx"
    entry.write_bytes(b"pk-v1")
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    repo = ArtifactRepository(db_client)
    await repo.create(Artifact(
        artifact_id="art_deck0001", agent_id="agent_x", user_id="user_y",
        session_id="s", title="deck", kind="application/vnd.officecli-live",
        file_path=f"{WS_REL}/deck.pptx", size_bytes=5,
        content_hash=hashlib.sha256(b"pk-v1").hexdigest(),
        created_at=past, updated_at=past,
    ))

    app = FastAPI()
    app.include_router(agents_router, prefix="/api/agents")
    yield {"client": TestClient(app), "db": db_client, "repo": repo, "entry": entry}


async def _history(db, aid):
    rows = await db.execute(
        "SELECT action FROM instance_artifact_history WHERE artifact_id = %s",
        params=(aid,), fetch=True)
    return [r["action"] for r in rows]


def test_commit_after_watch_edit_updates_hash_history_event(commit_env):
    commit_env["entry"].write_bytes(b"pk-v2-user-edited")
    r = commit_env["client"].post(
        "/api/agents/agent_x/artifacts/art_deck0001/office-edit-commit"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["content_hash"] == hashlib.sha256(b"pk-v2-user-edited").hexdigest()

    async def check():
        assert await _history(commit_env["db"], "art_deck0001") == ["user_edited"]
        rows = await commit_env["db"].execute(
            "SELECT payload_json FROM instance_artifact_events WHERE agent_id = %s",
            params=("agent_x",), fetch=True)
        payloads = [json.loads(x["payload_json"]) for x in rows]
        assert [p["action"] for p in payloads] == ["updated"]
    asyncio.get_event_loop().run_until_complete(check())


def test_commit_with_unchanged_hash_is_idempotent(commit_env):
    r = commit_env["client"].post(
        "/api/agents/agent_x/artifacts/art_deck0001/office-edit-commit"
    )
    assert r.status_code == 200
    async def check():
        assert await _history(commit_env["db"], "art_deck0001") == []
    asyncio.get_event_loop().run_until_complete(check())


def test_commit_rejects_non_office_kind(commit_env, tmp_path):
    async def seed():
        (tmp_path / "workspaces" / WS_REL / "notes.md").write_text("# x\n")
        await commit_env["repo"].create(Artifact(
            artifact_id="art_mdxx0001", agent_id="agent_x", user_id="user_y",
            session_id="s", title="notes", kind="text/markdown",
            file_path=f"{WS_REL}/notes.md", size_bytes=4,
            created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        ))
    asyncio.get_event_loop().run_until_complete(seed())
    r = commit_env["client"].post(
        "/api/agents/agent_x/artifacts/art_mdxx0001/office-edit-commit"
    )
    assert r.status_code == 400


# ── office-asset upload (T2 image replace) ───────────────────────────────────


def test_asset_upload_lands_next_to_entry(commit_env):
    r = commit_env["client"].post(
        "/api/agents/agent_x/artifacts/art_deck0001/office-asset",
        files={"file": ("logo.png", b"\x89PNG fake bytes", "image/png")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # absolute server path, inside the entry's directory, name randomized
    assert body["path"].startswith(str(commit_env["entry"].parent))
    assert body["path"].endswith(".png")
    assert "logo" in body["path"]
    assert pathlib.Path(body["path"]).read_bytes() == b"\x89PNG fake bytes"


def test_asset_upload_sanitizes_hostile_filename(commit_env):
    r = commit_env["client"].post(
        "/api/agents/agent_x/artifacts/art_deck0001/office-asset",
        files={"file": ("../../escape.png", b"x", "image/png")},
    )
    assert r.status_code == 200
    p = pathlib.Path(r.json()["path"])
    assert p.parent == commit_env["entry"].parent  # never escapes the entry dir


def test_asset_upload_rejects_non_office_artifact(commit_env):
    r = commit_env["client"].post(
        "/api/agents/agent_x/artifacts/art_mdxx0001/office-asset",
        files={"file": ("x.png", b"x", "image/png")},
    )
    assert r.status_code in (400, 404)


# ── review #334 r2 I6: the gates and the sanitizer get their own pins ────────


def test_put_content_oversized_declared_length_is_413(commit_env, monkeypatch, tmp_path):
    """The route-level fast reject fires on the DECLARED length before the
    service is ever reached."""
    import backend.routes.agents.artifacts as agents_mod

    called = []

    class _Svc:
        def __init__(self, db):
            pass

        async def save_user_content(self, **kw):
            called.append(kw)

    monkeypatch.setattr(agents_mod, "ArtifactService", _Svc)
    big = "x" * (28 * 1024 * 1024)
    r = commit_env["client"].put(
        "/api/agents/agent_x/artifacts/art_deck0001/content",
        json={"content": big, "base_hash": "h"},
    )
    assert r.status_code == 413
    assert called == []


def test_asset_upload_chunked_oversize_leaves_no_partial_file(commit_env):
    """No Content-Length (chunked): the STREAMED cap must fire, and the entry
    directory must be byte-for-byte what it was — a partial file would be
    served by the raw route and counted by _dir_size."""
    entry_dir = commit_env["entry"].parent
    before = sorted(os.listdir(entry_dir))

    def gen():
        for _ in range(11):
            yield b"x" * (1024 * 1024)

    boundary = "testboundary123"
    head = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="big.png"\r\n'
        "Content-Type: image/png\r\n\r\n"
    ).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()

    def body():
        yield head
        yield from gen()
        yield tail

    r = commit_env["client"].post(
        "/api/agents/agent_x/artifacts/art_deck0001/office-asset",
        content=body(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    assert r.status_code == 413
    assert sorted(os.listdir(entry_dir)) == before


@pytest.mark.parametrize(
    "name,expect",
    [
        ("../../etc/passwd", "passwd"),          # traversal reduced to basename
        ("グラフ図.png", "グラフ図.png"),          # kana survives \w
        ("диаграмма.png", "диаграмма.png"),      # cyrillic survives
        ("한글차트.png", "한글차트.png"),          # hangul survives
    ],
)
def test_asset_filename_sanitize_keeps_every_script(commit_env, name, expect):
    r = commit_env["client"].post(
        "/api/agents/agent_x/artifacts/art_deck0001/office-asset",
        files={"file": (name, b"data", "image/png")},
    )
    assert r.status_code == 200, r.text
    p = pathlib.Path(r.json()["path"])
    assert p.parent == commit_env["entry"].parent
    assert p.name.split("_", 1)[1] == expect
    p.unlink()


def test_asset_long_filename_truncates_but_keeps_extension(commit_env):
    """review #334 r2 M2: truncation must not eat the extension — the raw
    route serves by extension and a suffixless image renders as nothing."""
    r = commit_env["client"].post(
        "/api/agents/agent_x/artifacts/art_deck0001/office-asset",
        files={"file": ("a" * 300 + ".png", b"data", "image/png")},
    )
    assert r.status_code == 200
    p = pathlib.Path(r.json()["path"])
    assert p.name.endswith(".png")
    assert len(p.name) <= 140
    p.unlink()


# ── review #334 r3 I4: the ONLY structurally sound gate gets its pins ────────


def _edit_app(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    async def fake_lookup(request, artifact_id):
        return ("user_y", "agent_x", "/abs/deck.pptx", "deck.pptx")

    monkeypatch.setattr(owp, "_lookup_office_file", fake_lookup)
    monkeypatch.setattr(owp, "is_cloud_mode", lambda: False)
    monkeypatch.setattr(owp, "ensure_watch", lambda *a: 26320)
    app = FastAPI()
    app.include_router(owp.router, prefix="/api")
    return TestClient(app)


def test_edit_endpoint_declared_oversize_is_413_upstream_untouched(monkeypatch, proxy_client):
    client = _edit_app(monkeypatch)
    r = client.post(
        "/api/office-watch/edit?artifact_id=art_deck0001",
        content=b"[]",
        headers={"Content-Length": str(65 * 1024), "Content-Type": "application/json"},
    )
    assert r.status_code == 413
    assert proxy_client["calls"] == []


def test_edit_endpoint_chunked_oversize_is_413_upstream_untouched(monkeypatch, proxy_client):
    """Chunked (no Content-Length): only the streamed accumulation can catch
    it — this is the whole point of the second gate."""
    client = _edit_app(monkeypatch)

    def gen():
        for _ in range(65):
            yield b"[" * 1024  # not valid JSON, but the 413 must fire FIRST

    r = client.post(
        "/api/office-watch/edit?artifact_id=art_deck0001",
        content=gen(),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413
    assert proxy_client["calls"] == []


def test_edit_endpoint_non_json_body_is_422(monkeypatch, proxy_client):
    client = _edit_app(monkeypatch)
    r = client.post(
        "/api/office-watch/edit?artifact_id=art_deck0001",
        content=b"not json",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 422
    assert proxy_client["calls"] == []


def test_edit_endpoint_non_array_body_is_422(monkeypatch, proxy_client):
    client = _edit_app(monkeypatch)
    r = client.post(
        "/api/office-watch/edit?artifact_id=art_deck0001",
        json={"a": 1},
    )
    assert r.status_code == 422
    assert proxy_client["calls"] == []
