"""
@file_name: test_job_complex_creation_order.py
@author: Bin Liang
@date: 2026-09-09
@description: B-16 — route-level proof that POST /api/jobs/complex creates
dependencies before their dependents (forward references no longer KeyError)
and rejects a dependency cycle with 400 naming the task_keys, instead of
silently deadlocking every job in it at BLOCKED forever.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import narranexus_plugins.job_module.routes as jobs_mod
from narranexus_plugins.job_module.routes import router as jobs_router

AGENT_ID = "agent_mine"
USER_ID = "u1"


@pytest.fixture
def app_and_calls(monkeypatch):
    calls = []

    class _FakeJobService:
        def __init__(self, _db):
            pass

        async def create_job_with_instance(self, **kwargs):
            calls.append(kwargs["title"])
            job_id = f"job_{kwargs['title']}"
            return {"success": True, "job_id": job_id}

    monkeypatch.setattr(
        "narranexus_plugins.job_module.job_service.JobInstanceService",
        _FakeJobService,
    )

    # Ownership/identity are resolved through the plugin-host seam
    # (narranexus.sdk.web), imported into this module's namespace as local
    # names — patch those directly instead of standing up a full WEB_HOST
    # registration, which is unrelated to what this test is proving (B-16's
    # topological sort, not the ownership gate — that's test_jobs_owner_gate.py's job).
    async def _require_owner(request, agent_id):
        assert agent_id == AGENT_ID

    async def _current_user_id(request):
        return USER_ID

    monkeypatch.setattr(jobs_mod, "require_agent_owner", _require_owner)
    monkeypatch.setattr(jobs_mod, "current_user_id", _current_user_id)

    async def _job_db():
        return object()

    monkeypatch.setattr(jobs_mod, "get_db_client", _job_db)

    app = FastAPI()

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        request.state.user_id = request.headers.get("x-test-user") or None
        return await call_next(request)

    app.include_router(jobs_router, prefix="/api/jobs")
    client = TestClient(app, raise_server_exceptions=False)
    return client, calls


def _post(client, jobs):
    return client.post(
        "/api/jobs/complex",
        headers={"x-test-user": USER_ID},
        json={"agent_id": AGENT_ID, "jobs": jobs},
    )


def test_forward_reference_creates_dependency_first(app_and_calls):
    client, calls = app_and_calls
    # "b" depends on "a", but "a" appears LATER in the request body — the
    # pre-fix code raised KeyError on task_key_to_job_id["a"] here.
    jobs = [
        {"task_key": "b", "title": "b", "depends_on": ["a"]},
        {"task_key": "a", "title": "a", "depends_on": []},
    ]

    resp = _post(client, jobs)

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["jobs_created"] == 2
    # "a" (the dependency) must be created before "b" (its dependent).
    assert calls.index("a") < calls.index("b")


def test_dependency_cycle_returns_400_naming_task_keys(app_and_calls):
    client, calls = app_and_calls
    jobs = [
        {"task_key": "a", "title": "a", "depends_on": ["b"]},
        {"task_key": "b", "title": "b", "depends_on": ["a"]},
    ]

    resp = _post(client, jobs)

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "a" in detail and "b" in detail
    # Nothing gets created when the graph can't be sorted.
    assert calls == []


def test_duplicate_task_key_is_rejected_with_400(app_and_calls):
    """review I9: the topological sort keys jobs by task_key, so a repeated
    key would silently keep only the LAST job and answer success with one
    job_id fewer. Structural request errors are 400, like a cycle."""
    client, calls = app_and_calls
    jobs = [
        {"task_key": "a", "title": "first a", "depends_on": []},
        {"task_key": "b", "title": "b", "depends_on": ["a"]},
        {"task_key": "a", "title": "second a", "depends_on": []},
    ]

    resp = _post(client, jobs)

    assert resp.status_code == 400
    assert "duplicate task_key" in resp.json()["detail"]
    assert "a" in resp.json()["detail"]
    assert calls == []


def test_unique_task_keys_create_every_job(app_and_calls):
    client, calls = app_and_calls
    jobs = [
        {"task_key": "a", "title": "a", "depends_on": []},
        {"task_key": "b", "title": "b", "depends_on": ["a"]},
        {"task_key": "c", "title": "c", "depends_on": []},
    ]

    resp = _post(client, jobs)

    assert resp.status_code == 200
    assert resp.json()["jobs_created"] == 3
    assert sorted(calls) == ["a", "b", "c"]
