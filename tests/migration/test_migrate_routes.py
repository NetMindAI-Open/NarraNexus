"""
Agent Migration routes — local-only gate + scan/detect surface.
"""
from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.routes.migrate as mig


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(mig.router, prefix="/api/migrate")
    return TestClient(app)


def _mk_claude(home):
    d = home / ".claude"
    d.mkdir(parents=True)
    (d / "CLAUDE.md").write_text("You are a Claude Code agent.", encoding="utf-8")
    (d / ".mcp.json").write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    return d


def test_scan_blocked_on_cloud(tmp_path, monkeypatch):
    monkeypatch.setattr(mig, "is_cloud_mode", lambda: True)
    r = _client().post("/api/migrate/scan", json={"path": str(_mk_claude(tmp_path))})
    assert r.status_code == 503
    assert "migration_local_only" in r.json()["detail"]


def test_detect_blocked_on_cloud(monkeypatch):
    monkeypatch.setattr(mig, "is_cloud_mode", lambda: True)
    assert _client().get("/api/migrate/detect").status_code == 503


def test_scan_local_returns_standardized_json(tmp_path, monkeypatch):
    monkeypatch.setattr(mig, "is_cloud_mode", lambda: False)
    d = _mk_claude(tmp_path)
    r = _client().post("/api/migrate/scan", json={"path": str(d)})
    assert r.status_code == 200
    body = r.json()
    assert body["schema_version"] == "1.0"
    assert body["source"]["framework"] == "claude_code"
    assert "Claude Code agent" in body["agent"]["system_prompt"]


def test_scan_missing_source_404(tmp_path, monkeypatch):
    monkeypatch.setattr(mig, "is_cloud_mode", lambda: False)
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path))
    r = _client().post("/api/migrate/scan", json={})  # empty home, no path
    assert r.status_code == 404


_MIN_IMPORT = {
    "schema_version": "1.0",
    "source": {"framework": "claude_code", "detected_path": "/x", "detection_confidence": "high"},
    "agent": {"name": "A", "system_prompt": "hi", "description": ""},
}


def test_apply_blocked_on_cloud(monkeypatch):
    # Agent Migration is desktop/local only — apply must 503 on cloud too.
    monkeypatch.setattr(mig, "is_cloud_mode", lambda: True)
    r = _client().post("/api/migrate/apply", json={"import_data": _MIN_IMPORT})
    assert r.status_code == 503
    assert "migration_local_only" in r.json()["detail"]


def test_apply_rejects_foreign_agent_id(monkeypatch):
    # Reusing an agent you don't own must 403 (IDOR): the agent_id is
    # attacker-controlled, so ownership is verified before any write.
    monkeypatch.setattr(mig, "is_cloud_mode", lambda: False)

    async def _uid(_request):
        return "attacker"
    class _DB:
        async def get_one(self, table, filt):
            return {"agent_id": filt["agent_id"], "created_by": "victim"}
    async def _db():
        return _DB()
    monkeypatch.setattr(mig, "resolve_current_user_id", _uid)
    monkeypatch.setattr(mig, "get_db_client", _db)

    r = _client().post(
        "/api/migrate/apply",
        json={"import_data": _MIN_IMPORT, "agent_id": "agent_victim123"},
    )
    assert r.status_code == 403
    assert "do not own" in r.json()["detail"]


def _identified_client() -> TestClient:
    """Like _client(), plus the identity auth_middleware supplies in prod — /hurry
    is a write-ish route and resolves the caller like apply does."""
    from fastapi import Request

    app = FastAPI()

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        request.state.user_id = request.headers.get("X-User-Id") or None
        return await call_next(request)

    app.include_router(mig.router, prefix="/api/migrate")
    return TestClient(app)


def test_hurry_marks_the_import_and_is_local_only(monkeypatch):
    """POST /hurry is how the UI reaches an apply that is already writing: the
    apply keeps going but stops summarizing (see migration/hurry.py)."""
    from narranexus.platform.migration import hurry

    client = _identified_client()
    headers = {"X-User-Id": "u1"}

    monkeypatch.setattr(mig, "is_cloud_mode", lambda: True)
    r = client.post("/api/migrate/hurry", json={"import_id": "imp_x"}, headers=headers)
    assert r.status_code == 503
    assert hurry.is_hurried("imp_x") is False

    monkeypatch.setattr(mig, "is_cloud_mode", lambda: False)
    r = client.post("/api/migrate/hurry", json={"import_id": "imp_x"}, headers=headers)
    assert r.status_code == 200 and r.json()["success"] is True
    assert hurry.is_hurried("imp_x") is True
    hurry.clear("imp_x")
