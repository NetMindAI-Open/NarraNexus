"""
@file_name: test_jobs_owner_gate.py
@author:
@date: 2026-08-12
@description: Route-level proof that the job-id-addressed routes enforce
agent ownership (SEC-02 / Mark's IDOR batch).

`GET /api/jobs/{job_id}` and `PUT /api/jobs/{job_id}/cancel` previously
queried `instance_jobs` by `job_id` alone — any logged-in user who knew a
job_id could read another user's job (title/payload/last_error) and cancel
it. These tests pin the fix: the routes resolve the job's owning agent and
run it through the canonical `assert_owned` helper before doing anything.

A companion unit test pins the `last_error` scrub — the executor hostname
(`nx-exec-<user>-<hash>`) must never reach the API response.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes._ownership as own
import narranexus_plugins.job_module.routes as jobs_mod
from narranexus_plugins.job_module.routes import router as jobs_router, job_row_to_response


class _FakeDB:
    """Minimal db double: one job row keyed by job_id."""

    def __init__(self, row):
        self._row = row

    async def get_one(self, table, filters=None, **_):
        if self._row and filters and filters.get("job_id") == self._row["job_id"]:
            return dict(self._row)
        return None


@pytest.fixture
def client(monkeypatch):
    # A job that belongs to agent_theirs, owned by user u2.
    row = {
        "job_id": "job_abcd1234",
        "agent_id": "agent_theirs",
        "user_id": "u2",
        "title": "secret",
        "status": "active",
    }

    async def _job_db():
        return _FakeDB(row)

    monkeypatch.setattr(jobs_mod, "get_db_client", _job_db)

    async def _own_db():
        return object()

    monkeypatch.setattr(own, "get_db_client", _own_db)

    async def _resolve(self, agent_id):
        return {"agent_mine": "u1", "agent_theirs": "u2"}.get(agent_id, "")

    monkeypatch.setattr(own.AgentRepository, "resolve_owner", _resolve)

    app = FastAPI()

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        request.state.user_id = request.headers.get("x-test-user") or None
        return await call_next(request)

    app.include_router(jobs_router, prefix="/api/jobs")
    return TestClient(app, raise_server_exceptions=False)


def test_get_job_details_denies_non_owner(client):
    r = client.get("/api/jobs/job_abcd1234", headers={"x-test-user": "u1"})
    assert r.status_code == 403


def test_cancel_job_denies_non_owner(client):
    r = client.put("/api/jobs/job_abcd1234/cancel", headers={"x-test-user": "u1"})
    assert r.status_code == 403


def test_get_job_details_allows_owner(client):
    # The gate's failure mode is over-strictness (403 everyone), not just
    # under-strictness — pin that the real owner still gets through.
    r = client.get("/api/jobs/job_abcd1234", headers={"x-test-user": "u2"})
    assert r.status_code == 200
    assert r.json()["success"] is True


def test_create_job_complex_uses_authenticated_identity_not_body(client, monkeypatch):
    captured = {}

    class _FakeJobService:
        def __init__(self, _db):
            pass

        async def create_job_with_instance(self, **kwargs):
            captured["user_id"] = kwargs.get("user_id")
            return {"success": True, "job_id": "job_new"}

    monkeypatch.setattr(
        "narranexus_plugins.job_module.job_service.JobInstanceService",
        _FakeJobService,
    )
    r = client.post(
        "/api/jobs/complex",
        headers={"x-test-user": "u2"},  # the real owner of agent_theirs
        json={
            "agent_id": "agent_theirs",
            "user_id": "u_evil",  # extra field — must be ignored, not trusted
            "jobs": [{"task_key": "t1", "title": "ok"}],
        },
    )
    assert r.status_code == 200
    # The job is created under the authenticated caller, never body.user_id.
    assert captured["user_id"] == "u2"


def test_create_job_complex_denies_non_owner(client):
    # POST /complex used to trust the body's agent_id/user_id with no ownership
    # check — a write+execute IDOR strictly worse than reading/cancelling.
    r = client.post(
        "/api/jobs/complex",
        headers={"x-test-user": "u1"},
        json={
            "agent_id": "agent_theirs",
            "user_id": "u2",
            "jobs": [{"task_key": "t1", "title": "evil"}],
        },
    )
    assert r.status_code == 403


def test_last_error_scrubs_executor_hostname():
    row = {
        "job_id": "job_x",
        "agent_id": "a",
        "user_id": "u1",
        "title": "t",
        "job_type": "one_off",
        "status": "active",
        "last_error": "connect to nx-exec-u2abc-9f8e7d timed out",
    }
    resp = job_row_to_response(row)
    assert "nx-exec-u2abc-9f8e7d" not in (resp.last_error or "")
