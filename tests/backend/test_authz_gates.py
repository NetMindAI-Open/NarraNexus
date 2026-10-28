"""
@file_name: test_authz_gates.py
@author: Bin Liang
@date: 2026-10-28
@description: Route-level authorization / hygiene gates, each pinned by a test
that goes red when its guard is reverted:
- bundle export refuses a team the exporter does not own (BundleForbidden -> 403);
- /api/auth/agents never previews a public agent owned by someone else;
- marketplace skill install requires owning the target agent;
- the shared staff gate (admin/_staff.py) and /api/admin/runtime/workers;
- admin routes answer 403 before the body is validated (no 422 schema leak);
- skills routes do not echo exception text.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

# --- bundle export: team ownership -------------------------------------------

def test_export_refuses_foreign_team():
    from narranexus.platform.bundle.builder import BundleForbidden, assert_team_owned
    assert_team_owned({"team_id": "team_1", "owner_user_id": "me"}, "team_1", "me")
    assert_team_owned(None, "team_1", "me")  # unknown team: nothing to leak
    with pytest.raises(BundleForbidden):
        assert_team_owned({"team_id": "team_1", "owner_user_id": "victim"}, "team_1", "me")


# --- /api/auth/agents preview isolation --------------------------------------

@pytest.fixture
def auth_client(monkeypatch, db_client, tmp_path):
    import backend.routes.auth as auth_mod
    from narranexus.platform.settings import settings
    monkeypatch.setattr(settings, "base_working_path", str(tmp_path))
    app = FastAPI()

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        request.state.user_id = request.headers.get("X-User-Id") or None
        return await call_next(request)

    async def _get_db():
        return db_client
    monkeypatch.setattr(auth_mod, "get_db_client", _get_db)
    app.include_router(auth_mod.router, prefix="/api/auth")
    return TestClient(app, raise_server_exceptions=False)


async def test_public_agent_preview_is_not_shown_to_strangers(auth_client, db_client):
    await db_client.insert("agents", {"agent_id": "a_pub", "agent_name": "pub", "created_by": "victim", "is_public": 1})
    await db_client.insert("agents", {"agent_id": "a_mine", "agent_name": "mine", "created_by": "me", "is_public": 0})
    for aid, uid, text in (("a_pub", "victim", "victim-private-reply"), ("a_mine", "me", "my-reply")):
        await db_client.insert("events", {
            "event_id": f"evt_{aid}", "agent_id": aid, "user_id": uid, "trigger": "web",
            "trigger_source": "web", "final_output": text, "state": "completed",
            "started_at": "2026-10-28 08:00:00", "finished_at": "2026-10-28 08:00:01",
        })
    r = auth_client.get("/api/auth/agents", headers={"X-User-Id": "me"})
    assert r.status_code == 200, r.text
    assert "victim-private-reply" not in r.text
    assert "my-reply" in r.text


# --- marketplace install: owner only ------------------------------------------

async def test_marketplace_install_refuses_foreign_agent(monkeypatch, db_client):
    import backend.routes._ownership as own
    import backend.routes.marketplace_skills as mk
    await db_client.insert("agents", {"agent_id": "a_victim", "agent_name": "v", "created_by": "victim"})

    async def _db():
        return db_client
    monkeypatch.setattr(own, "get_db_client", _db)
    called = []

    from types import SimpleNamespace

    class _Svc:
        async def install(self, *a, **k):
            called.append(a)
            return SimpleNamespace(
                status="installed", skill=None, config_required=[], warnings=[], replaced_version=None,
            )
    monkeypatch.setattr(mk, "SkillMarketplaceService", _Svc)
    app = FastAPI()

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        request.state.user_id = request.headers.get("X-User-Id") or None
        return await call_next(request)
    app.include_router(mk.router, prefix="/api/marketplace/skills")
    c = TestClient(app, raise_server_exceptions=False)
    r = c.post("/api/marketplace/skills/sk1/install", json={"agent_id": "a_victim"}, headers={"X-User-Id": "me"})
    assert r.status_code in (403, 404), r.text
    assert called == []
    r = c.post("/api/marketplace/skills/sk1/install", json={"agent_id": "a_victim"}, headers={"X-User-Id": "victim"})
    assert r.status_code == 200 and called


# --- admin: workers staff gate + secret before body ----------------------------

def _admin_app(monkeypatch, db_client, cloud: bool):
    import backend.routes.admin._staff as staff
    import backend.routes.admin.runtime as rt
    import backend.routes.admin.suspend as sus

    async def _db():
        return db_client
    monkeypatch.setattr(rt, "get_db_client", _db)
    monkeypatch.setattr(staff, "is_cloud_mode", lambda: cloud)
    monkeypatch.setattr(rt.settings, "admin_secret_key", "sekrit")
    app = FastAPI()

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        request.state.user_id = request.headers.get("X-User-Id") or None
        request.state.role = request.headers.get("X-Test-Role") or ""
        return await call_next(request)
    app.include_router(rt.router)
    app.include_router(sus.router)
    return TestClient(app, raise_server_exceptions=False)


def test_workers_is_staff_only_in_cloud(monkeypatch, db_client):
    c = _admin_app(monkeypatch, db_client, cloud=True)
    assert c.get("/api/admin/runtime/workers", headers={"X-User-Id": "u"}).status_code == 403
    assert c.get("/api/admin/runtime/workers", headers={"X-User-Id": "u", "X-Test-Role": "staff"}).status_code == 200


def test_workers_stays_open_in_local_mode(monkeypatch, db_client):
    c = _admin_app(monkeypatch, db_client, cloud=False)
    assert c.get("/api/admin/runtime/workers", headers={"X-User-Id": "u"}).status_code == 200


def _staff_gate_app(monkeypatch, cloud: bool, role):
    import backend.routes.admin._staff as staff
    from fastapi import Depends

    monkeypatch.setattr(staff, "is_cloud_mode", lambda: cloud)
    app = FastAPI()

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        if role is not None:
            request.state.role = role
        return await call_next(request)

    @app.get("/probe", dependencies=[Depends(staff.require_staff_in_cloud)])
    async def probe():
        return {"ok": True}
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("role", [None, "", "user"])
def test_staff_gate_refuses_non_staff_in_cloud(monkeypatch, role):
    # role None in cloud means the middleware did not resolve a role: refuse,
    # never read it as "local mode".
    assert _staff_gate_app(monkeypatch, True, role).get("/probe").status_code == 403


def test_staff_gate_admits_staff_in_cloud(monkeypatch):
    assert _staff_gate_app(monkeypatch, True, "staff").get("/probe").status_code == 200


@pytest.mark.parametrize("role", [None, "user"])
def test_staff_gate_allows_everyone_in_local_mode(monkeypatch, role):
    assert _staff_gate_app(monkeypatch, False, role).get("/probe").status_code == 200


def test_admin_secret_is_checked_before_the_body(monkeypatch, db_client):
    c = _admin_app(monkeypatch, db_client, cloud=True)
    r = c.post("/api/admin/suspend", json={"not": "the schema"})
    assert r.status_code == 403, r.text
    assert "user_id" not in r.text  # no 422 field listing
    r = c.post("/api/admin/suspend", json={"not": "the schema"}, headers={"X-Admin-Secret": "sekrit"})
    assert r.status_code == 422


# --- skills routes: no exception echo -------------------------------------------

def test_skills_routes_do_not_echo_exception_text(monkeypatch):
    from narranexus_plugins.skill_module import routes as sk

    async def _user(_request, _agent_id=None):
        return "u1"

    class _Boom:
        def list_skills(self, include_disabled=False):
            raise RuntimeError("/opt/narranexus/workspaces/secret/path leaked")
    monkeypatch.setattr(sk, "require_workspace_agent_owner", _user, raising=False)
    monkeypatch.setattr(sk, "current_user_id", _user, raising=False)
    monkeypatch.setattr(sk, "_get_skill_module", lambda agent_id, user_id: _Boom())
    app = FastAPI()
    app.include_router(sk.router, prefix="/api/skills")
    r = TestClient(app, raise_server_exceptions=False).get("/api/skills", params={"agent_id": "a1"})
    assert r.status_code == 500
    assert "secret/path" not in r.text and "RuntimeError" not in r.text


# --- bundle export route: ownership refusal is a 403 --------------------------

def _export_client(monkeypatch, error):
    import backend.routes.bundle as bundle_mod

    async def fake_user_id(request):
        return "me"

    async def fake_build(user_id, selection, out_path):
        raise error

    monkeypatch.setattr(bundle_mod, "_user_id_for_request", fake_user_id)
    monkeypatch.setattr(bundle_mod, "build_bundle", fake_build)
    app = FastAPI()
    app.include_router(bundle_mod.router, prefix="/api/bundle")
    return TestClient(app)


def test_export_of_a_foreign_team_is_403(monkeypatch):
    from narranexus.platform.bundle.builder import BundleForbidden
    client = _export_client(monkeypatch, BundleForbidden("Forbidden: team team_v not owned by me"))
    r = client.post("/api/bundle/export", json={"agent_ids": ["agent_a"], "team_id": "team_v"})
    assert r.status_code == 403, r.text
    assert "team_v" not in r.text


def test_export_does_not_sniff_forbidden_prose(monkeypatch):
    # Only the typed verdict maps to 403; a plain ValueError whose text happens
    # to start with "Forbidden" is a server fault and keeps its traceback.
    client = _export_client(monkeypatch, ValueError("Forbidden: something upstream"))
    r = client.post("/api/bundle/export", json={"agent_ids": ["agent_a"]})
    assert r.status_code == 500, r.text


def test_export_other_failures_stay_500(monkeypatch):
    client = _export_client(monkeypatch, ValueError("disk on fire"))
    r = client.post("/api/bundle/export", json={"agent_ids": ["agent_a"]})
    assert r.status_code == 500, r.text
    assert "disk on fire" not in r.text
