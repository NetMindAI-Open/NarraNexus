"""
@file_name: test_job_seam_routes.py
@author:
@date: 2026-08-10
@description: Route-level tests for the job MCP data-access-seam endpoints —
reads (GET /{agent_id}/jobs/{job_id}, POST .../search-semantic, .../search-keywords)
and writes (POST .../{job_id}/update, POST .../jobs create, PUT .../{job_id}/pause,
PUT .../{job_id}/cancel). Owner-gated byte-parity twins of the JobModule tools.
Mirrors the test_narrative_routes.py fixture shape.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes._ownership as own
import narranexus_plugins.job_module.routes_agent as jr
import narranexus.platform.agent_framework.api_config as japi
import narranexus_plugins.job_module._job_reads as jreads
import narranexus_plugins.job_module._job_writes as jwrites
import narranexus_plugins.job_module.job_service as jsvc


class _V:
    def __init__(self, v):
        self.value = v


class _FakeJob:
    def __init__(self, job_id, agent_id):
        self.job_id = job_id
        self.agent_id = agent_id
        self.user_id = "u1"
        self.instance_id = "job_inst"
        self.title = "T"
        self.description = "D"
        self.payload = {}
        self.job_type = _V("one_off")
        self.trigger_config = None
        self.status = _V("active")
        self.notification_method = "none"
        self.next_run_at_local = None
        self.next_run_tz = "UTC"
        self.last_run_at_local = None
        self.last_run_tz = "UTC"
        self.related_entity_id = None
        self.narrative_id = None
        self.iteration_count = 0
        self.process = "proc"
        self.last_error = None
        self.created_at = None
        self.updated_at = None


class _FakeJobRepo:
    def __init__(self, db):
        pass

    async def get_job(self, job_id):
        # job_mine owned by agent_mine; job_theirs owned by agent_theirs
        if job_id == "job_mine":
            return _FakeJob("job_mine", "agent_mine")
        if job_id == "job_theirs":
            return _FakeJob("job_theirs", "agent_theirs")
        return None

    async def search_keyword(self, agent_id, query, user_id, status, limit):
        return [(_FakeJob("job_mine", agent_id), 0.9)]

    async def search_by_keywords(self, agent_id, keywords, user_id, status, limit):
        return [_FakeJob("job_mine", agent_id)]


@pytest.fixture
def client(monkeypatch):
    async def _db():
        return object()

    monkeypatch.setattr(own, "get_db_client", _db)
    monkeypatch.setattr(jr, "get_db_client", _db)
    monkeypatch.setattr(jreads, "JobRepository", _FakeJobRepo)

    async def _resolve(self, agent_id):
        return {"agent_mine": "u1", "agent_theirs": "u2"}.get(agent_id, "")

    monkeypatch.setattr(own.AgentRepository, "resolve_owner", _resolve)

    app = FastAPI()

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        request.state.user_id = request.headers.get("x-test-user") or None
        return await call_next(request)

    app.include_router(jr.router, prefix="/api/agents")
    return TestClient(app)


OWNER = {"x-test-user": "u1"}


def test_job_by_id_non_owner_is_denied(client):
    r = client.get("/api/agents/agent_theirs/jobs/job_theirs", headers=OWNER)
    assert r.status_code == 403


def test_job_search_semantic_non_owner_is_denied(client):
    r = client.post("/api/agents/agent_theirs/jobs/search-semantic", headers=OWNER, json={"query": "x"})
    assert r.status_code == 403


def test_job_by_id_returns_detail(client):
    r = client.get("/api/agents/agent_mine/jobs/job_mine", headers=OWNER)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["job"]["job_id"] == "job_mine"
    assert body["job"]["process"] == "proc"


def test_job_by_id_cross_agent_is_access_denied(client):
    # job_theirs is owned by agent_theirs; agent_mine (the caller's own agent)
    # must not read it.
    r = client.get("/api/agents/agent_mine/jobs/job_theirs", headers=OWNER)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "Access denied" in body["error"]


def test_job_by_id_not_found(client):
    r = client.get("/api/agents/agent_mine/jobs/job_ghost", headers=OWNER)
    assert r.status_code == 200
    assert r.json() == {"success": False, "error": "Job not found: job_ghost"}


def test_job_search_semantic_returns_results(client):
    r = client.post("/api/agents/agent_mine/jobs/search-semantic", headers=OWNER, json={"query": "news"})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["total_results"] == 1
    assert body["jobs"][0]["similarity_score"] == 0.9


def test_job_search_keywords_returns_results(client):
    r = client.post("/api/agents/agent_mine/jobs/search-keywords", headers=OWNER, json={"keywords": ["news"]})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["total_results"] == 1


def test_job_search_keywords_requires_keywords(client):
    r = client.post("/api/agents/agent_mine/jobs/search-keywords", headers=OWNER, json={"keywords": []})
    assert r.status_code == 422


# --------------------------------------------------------------------------- job_update twin


def _patch_update(monkeypatch, *, job, result):
    class _Repo:
        def __init__(self, db):
            pass

        async def get_job(self, job_id):
            return job if (job and job.job_id == job_id) else None

    class _Svc:
        def __init__(self, db):
            pass

        async def update_job(self, job_id, updates, agent_id):
            return result

    monkeypatch.setattr(jwrites, "JobRepository", _Repo)
    monkeypatch.setattr(jsvc, "JobInstanceService", _Svc)


def test_job_update_non_owner_is_denied(client):
    r = client.post("/api/agents/agent_theirs/jobs/job_theirs/update", headers=OWNER, json={"title": "x"})
    assert r.status_code == 403


def test_job_update_success(client, monkeypatch):
    job = _FakeJob("job_mine", "agent_mine")
    _patch_update(monkeypatch, job=job, result={"success": True, "job_id": "job_mine", "updated_fields": ["title"], "message": "Updated"})
    r = client.post("/api/agents/agent_mine/jobs/job_mine/update", headers=OWNER, json={"title": "New"})
    assert r.status_code == 200
    assert r.json() == {"success": True, "job_id": "job_mine", "updated_fields": ["title"], "message": "Updated"}


def test_job_update_cross_agent_is_not_found(client, monkeypatch):
    job = _FakeJob("job_theirs", "agent_theirs")
    _patch_update(monkeypatch, job=job, result=None)
    # caller owns agent_mine; job_theirs belongs to agent_theirs -> not found
    r = client.post("/api/agents/agent_mine/jobs/job_theirs/update", headers=OWNER, json={"title": "New"})
    assert r.status_code == 200
    assert r.json() == {"success": False, "job_id": "job_theirs", "message": "Job job_theirs not found"}


def test_job_update_seam_body_forbids_unknown_fields(client, monkeypatch):
    # The seam write body is extra="forbid": a field that drifts (added to the
    # shared fn + MCP tool but not to the field contract) must 422 LOUDLY here,
    # not be silently dropped on the HttpStore path while DirectStore applies it.
    # Without extra="forbid" pydantic's default ignore would make this a 200.
    job = _FakeJob("job_mine", "agent_mine")
    _patch_update(monkeypatch, job=job, result={"success": True, "job_id": "job_mine",
                                                 "updated_fields": ["title"], "message": "Updated"})
    r = client.post("/api/agents/agent_mine/jobs/job_mine/update", headers=OWNER,
                    json={"title": "New", "totally_new_field": "x"})
    assert r.status_code == 422


def test_job_update_seam_body_accepts_the_known_fields(client, monkeypatch):
    # Guard the other side: every legitimate field must pass the forbid gate.
    job = _FakeJob("job_mine", "agent_mine")
    _patch_update(monkeypatch, job=job, result={"success": True, "job_id": "job_mine",
                                                 "updated_fields": [], "message": "Updated"})
    r = client.post("/api/agents/agent_mine/jobs/job_mine/update", headers=OWNER, json={
        "title": "t", "description": "d", "payload": "p", "guidance_text": "g",
        "trigger_config": {"cron": "0 8 * * *", "timezone": "UTC"}, "job_type": "scheduled",
        "next_run_time": None, "status": "paused", "related_entity_id": "nar_1",
    })
    assert r.status_code == 200


# --------------------------------------------------------------------------- job_create / job_pause / job_cancel twins


def _patch_create(monkeypatch, *, result, capture=None):
    """Fake create_job_from_args' collaborators: the owner LLM-context setup
    (no-op) and JobInstanceService.create_job_with_instance."""
    async def _noop(agent_id):
        return None

    monkeypatch.setattr(japi, "setup_mcp_llm_context", _noop)

    class _Svc:
        def __init__(self, db):
            pass

        async def create_job_with_instance(self, **kw):
            if capture is not None:
                capture.update(kw)
            return result

    monkeypatch.setattr(jsvc, "JobInstanceService", _Svc)


def _patch_pause_cancel(monkeypatch, *, job, rows=1):
    class _Repo:
        def __init__(self, db):
            pass

        async def get_job(self, job_id):
            return job if (job and job.job_id == job_id) else None

        async def pause_job(self, job_id):
            return rows

        async def cancel_job(self, job_id):
            return rows

    monkeypatch.setattr(jwrites, "JobRepository", _Repo)


_CREATE_BODY = {"user_id": "u1", "title": "T", "description": "d", "job_type": "one_off",
                "trigger_config": {"run_at": "2026-09-01T09:00:00", "timezone": "UTC"}, "payload": "p"}


def test_job_create_non_owner_is_denied(client):
    r = client.post("/api/agents/agent_theirs/jobs", headers=OWNER, json=_CREATE_BODY)
    assert r.status_code == 403


def test_job_create_success(client, monkeypatch):
    _patch_create(monkeypatch, result={"success": True, "job_id": "job_new",
                                        "instance_id": "job_i", "message": "Created"})
    r = client.post("/api/agents/agent_mine/jobs", headers=OWNER, json=_CREATE_BODY)
    assert r.status_code == 200
    assert r.json() == {"success": True, "job_id": "job_new", "instance_id": "job_i", "message": "Created"}


def test_job_create_seam_body_forbids_unknown_fields(client, monkeypatch):
    # Same loud-drift guard as job_update: an unknown create field must 422 here
    # (extra="forbid"), never be silently dropped on the HttpStore path.
    _patch_create(monkeypatch, result={"success": True})
    r = client.post("/api/agents/agent_mine/jobs", headers=OWNER, json={**_CREATE_BODY, "bogus": 1})
    assert r.status_code == 422


def test_job_create_seam_body_accepts_every_known_field(client, monkeypatch):
    # The other side of the forbid guard: all 12 fields the MCP tool +
    # create_job_from_args accept must pass extra="forbid" through the REAL route
    # (a fake handler can't prove this). If a field is added to the tool but not
    # to JobCreateSeamBody, this 422s — the drift is caught at its source.
    _patch_create(monkeypatch, result={"success": True, "job_id": "job_new"})
    r = client.post("/api/agents/agent_mine/jobs", headers=OWNER, json={
        "user_id": "u1", "title": "T", "description": "d", "job_type": "one_off",
        "trigger_config": {"run_at": "2026-09-01T09:00:00", "timezone": "UTC"},
        "payload": "p", "notification_method": "direct", "task_key": "tk1",
        "depends_on_job_ids": ["job_a1b2c3d4"], "related_entity_id": "u2",
        "narrative_id": "nar_1", "confirm_new": True,
    })
    assert r.status_code == 200
    assert r.json()["success"] is True


def test_job_pause_non_owner_is_denied(client):
    r = client.put("/api/agents/agent_theirs/jobs/job_theirs/pause", headers=OWNER)
    assert r.status_code == 403


def test_job_pause_success(client, monkeypatch):
    _patch_pause_cancel(monkeypatch, job=_FakeJob("job_mine", "agent_mine"))
    r = client.put("/api/agents/agent_mine/jobs/job_mine/pause", headers=OWNER)
    assert r.status_code == 200
    assert r.json() == {"success": True, "job_id": "job_mine", "status": "paused",
                        "message": "Job paused successfully"}


def test_job_pause_cross_agent_is_not_found(client, monkeypatch):
    _patch_pause_cancel(monkeypatch, job=_FakeJob("job_theirs", "agent_theirs"))
    r = client.put("/api/agents/agent_mine/jobs/job_theirs/pause", headers=OWNER)
    assert r.status_code == 200
    assert r.json() == {"success": False, "job_id": "job_theirs", "message": "Job job_theirs not found"}


def test_job_cancel_non_owner_is_denied(client):
    r = client.put("/api/agents/agent_theirs/jobs/job_theirs/cancel", headers=OWNER)
    assert r.status_code == 403


def test_job_cancel_success(client, monkeypatch):
    _patch_pause_cancel(monkeypatch, job=_FakeJob("job_mine", "agent_mine"))
    r = client.put("/api/agents/agent_mine/jobs/job_mine/cancel", headers=OWNER)
    assert r.status_code == 200
    assert r.json() == {"success": True, "job_id": "job_mine", "status": "cancelled",
                        "message": "Job cancelled successfully"}
