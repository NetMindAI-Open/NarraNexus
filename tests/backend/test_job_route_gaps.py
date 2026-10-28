"""
@file_name: test_job_route_gaps.py
@author:
@date: 2026-08-10
@description: Route-level tests for the four jobs.py endpoints added as the
backend half of the MCP data-access seam (PR-2): PUT /{job_id} (job_update),
PUT /{job_id}/pause (job_pause), GET /search/semantic (job_retrieval_semantic),
GET /search/keywords (job_retrieval_by_keywords).

Each endpoint mirrors the matching tool in
plugins/builtin.job/src/narranexus_plugins/job_module/_job_mcp_tools.py — same
JobRepository/JobInstanceService calls, same response shape. Ownership
(assert_owned) is exercised once per endpoint the same way
test_channel_routes_owner_gate.py pins channel routes to the canonical
helper; business-logic branches are covered by stubbing JobRepository /
JobInstanceService directly (the underlying repository/service logic already
has its own coverage elsewhere — job_recovery / job_reschedule tests).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes._ownership as own
import narranexus_plugins.job_module.routes as jobs_module
from narranexus_plugins.job_module import job_service as job_service_module
from narranexus.platform.schema import JobStatus, JobType


def _fake_job(**overrides) -> SimpleNamespace:
    base = dict(
        job_id="job1",
        agent_id="agent_mine",
        user_id="u1",
        instance_id="ins_job1",
        title="Existing title",
        description="Existing description",
        payload="Existing payload",
        job_type=JobType.ONE_OFF,
        trigger_config=None,
        status=JobStatus.ACTIVE,
        notification_method="direct",
        next_run_at_local=None,
        next_run_tz=None,
        last_run_at_local=None,
        last_run_tz=None,
        related_entity_id=None,
        narrative_id=None,
        iteration_count=0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def client(monkeypatch):
    async def _db():
        return object()

    monkeypatch.setattr(own, "get_db_client", _db)
    monkeypatch.setattr(jobs_module, "get_db_client", _db)

    async def _resolve(self, agent_id):
        return {"agent_mine": "u1", "agent_theirs": "u2"}.get(agent_id, "")

    monkeypatch.setattr(own.AgentRepository, "resolve_owner", _resolve)

    app = FastAPI()

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        request.state.user_id = request.headers.get("x-test-user") or None
        return await call_next(request)

    app.include_router(jobs_module.router, prefix="/api/jobs")
    return TestClient(app)


# ── PUT /{job_id} — job_update semantics ────────────────────────────────────


def test_update_job_denied_for_non_owner(client):
    r = client.put(
        "/api/jobs/job1",
        headers={"x-test-user": "u1"},
        json={"agent_id": "agent_theirs", "title": "New title"},
    )
    assert r.status_code == 403


def test_update_job_unknown_agent_reports_not_found(client):
    r = client.put(
        "/api/jobs/job1",
        headers={"x-test-user": "u1"},
        json={"agent_id": "agent_ghost", "title": "New title"},
    )
    assert r.status_code == 404


def test_update_job_missing_job_returns_success_false(client, monkeypatch):
    async def _get_job(self, job_id):
        return None

    monkeypatch.setattr(jobs_module.JobRepository, "get_job", _get_job)

    r = client.put(
        "/api/jobs/job_missing",
        headers={"x-test-user": "u1"},
        json={"agent_id": "agent_mine", "title": "New title"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "not found" in body["message"]


def test_update_job_rejects_job_owned_by_different_agent(client, monkeypatch):
    async def _get_job(self, job_id):
        return _fake_job(agent_id="agent_other")

    monkeypatch.setattr(jobs_module.JobRepository, "get_job", _get_job)

    r = client.put(
        "/api/jobs/job1",
        headers={"x-test-user": "u1"},
        json={"agent_id": "agent_mine", "title": "New title"},
    )
    body = r.json()
    assert body["success"] is False
    assert "not found" in body["message"]


def test_update_job_no_fields_to_update(client, monkeypatch):
    async def _get_job(self, job_id):
        return _fake_job()

    monkeypatch.setattr(jobs_module.JobRepository, "get_job", _get_job)

    r = client.put(
        "/api/jobs/job1",
        headers={"x-test-user": "u1"},
        json={"agent_id": "agent_mine"},
    )
    body = r.json()
    assert body["success"] is False
    assert body["message"] == "No fields to update"


def test_update_job_invalid_trigger_config(client, monkeypatch):
    async def _get_job(self, job_id):
        return _fake_job()

    monkeypatch.setattr(jobs_module.JobRepository, "get_job", _get_job)

    r = client.put(
        "/api/jobs/job1",
        headers={"x-test-user": "u1"},
        json={"agent_id": "agent_mine", "trigger_config": {"run_at": "2026-08-01T09:00:00"}},
    )
    body = r.json()
    assert body["success"] is False
    assert "Invalid trigger_config" in body["message"]


def test_update_job_success_delegates_to_service(client, monkeypatch):
    async def _get_job(self, job_id):
        return _fake_job()

    monkeypatch.setattr(jobs_module.JobRepository, "get_job", _get_job)

    captured = {}

    async def _update_job(self, job_id, updates, agent_id=None):
        captured["job_id"] = job_id
        captured["updates"] = dict(updates)
        captured["agent_id"] = agent_id
        return {
            "success": True,
            "job_id": job_id,
            "updated_fields": list(updates.keys()),
            "message": "Job updated successfully",
        }

    monkeypatch.setattr(job_service_module.JobInstanceService, "update_job", _update_job)

    r = client.put(
        "/api/jobs/job1",
        headers={"x-test-user": "u1"},
        json={"agent_id": "agent_mine", "title": "New title"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["updated_fields"] == ["title"]
    assert captured["agent_id"] == "agent_mine"
    assert captured["updates"] == {"title": "New title"}


def test_update_job_service_failure_shape(client, monkeypatch):
    async def _get_job(self, job_id):
        return _fake_job()

    monkeypatch.setattr(jobs_module.JobRepository, "get_job", _get_job)

    async def _update_job(self, job_id, updates, agent_id=None):
        return {"success": False, "job_id": job_id, "updated_fields": [], "message": "No changes made"}

    monkeypatch.setattr(job_service_module.JobInstanceService, "update_job", _update_job)

    r = client.put(
        "/api/jobs/job1",
        headers={"x-test-user": "u1"},
        json={"agent_id": "agent_mine", "title": "New title"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert body["message"] == "No changes made"


# ── PUT /{job_id}/pause — job_pause semantics ───────────────────────────────


def test_pause_job_denied_for_non_owner(client):
    r = client.put(
        "/api/jobs/job1/pause",
        headers={"x-test-user": "u1"},
        json={"agent_id": "agent_theirs"},
    )
    assert r.status_code == 403


def test_pause_job_not_found(client, monkeypatch):
    async def _get_job(self, job_id):
        return None

    monkeypatch.setattr(jobs_module.JobRepository, "get_job", _get_job)

    r = client.put("/api/jobs/job1/pause", headers={"x-test-user": "u1"}, json={"agent_id": "agent_mine"})
    body = r.json()
    assert body["success"] is False
    assert "not found" in body["message"]


def test_pause_job_rejects_job_owned_by_different_agent(client, monkeypatch):
    async def _get_job(self, job_id):
        return _fake_job(agent_id="agent_other")

    monkeypatch.setattr(jobs_module.JobRepository, "get_job", _get_job)

    r = client.put("/api/jobs/job1/pause", headers={"x-test-user": "u1"}, json={"agent_id": "agent_mine"})
    body = r.json()
    assert body["success"] is False
    assert "not found" in body["message"]


def test_pause_job_success(client, monkeypatch):
    async def _get_job(self, job_id):
        return _fake_job()

    monkeypatch.setattr(jobs_module.JobRepository, "get_job", _get_job)

    async def _pause_job(self, job_id):
        return 1

    monkeypatch.setattr(jobs_module.JobRepository, "pause_job", _pause_job)

    r = client.put("/api/jobs/job1/pause", headers={"x-test-user": "u1"}, json={"agent_id": "agent_mine"})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["status"] == "paused"


def test_pause_job_zero_rows_affected_reports_failure(client, monkeypatch):
    async def _get_job(self, job_id):
        return _fake_job()

    monkeypatch.setattr(jobs_module.JobRepository, "get_job", _get_job)

    async def _pause_job(self, job_id):
        return 0

    monkeypatch.setattr(jobs_module.JobRepository, "pause_job", _pause_job)

    r = client.put("/api/jobs/job1/pause", headers={"x-test-user": "u1"}, json={"agent_id": "agent_mine"})
    body = r.json()
    assert body["success"] is False
    assert body["message"] == "Failed to pause job"


# ── GET /search/semantic — job_retrieval_semantic semantics ────────────────


def test_search_semantic_denied_for_non_owner(client):
    r = client.get(
        "/api/jobs/search/semantic",
        headers={"x-test-user": "u1"},
        params={"agent_id": "agent_theirs", "query": "reminders"},
    )
    assert r.status_code == 403


def test_search_semantic_invalid_status(client):
    r = client.get(
        "/api/jobs/search/semantic",
        headers={"x-test-user": "u1"},
        params={"agent_id": "agent_mine", "query": "reminders", "status": "bogus"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "Invalid status" in body["error"]


def test_search_semantic_success(client, monkeypatch):
    async def _search_keyword(self, agent_id, query, user_id=None, status=None, limit=10):
        return [(_fake_job(title="Daily report"), 0.4231)]

    monkeypatch.setattr(jobs_module.JobRepository, "search_keyword", _search_keyword)

    r = client.get(
        "/api/jobs/search/semantic",
        headers={"x-test-user": "u1"},
        params={"agent_id": "agent_mine", "query": "daily report"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["total_results"] == 1
    assert body["jobs"][0]["title"] == "Daily report"
    assert body["jobs"][0]["similarity_score"] == 0.4231


def test_search_semantic_failure_shape(client, monkeypatch):
    async def _search_keyword(self, agent_id, query, user_id=None, status=None, limit=10):
        raise RuntimeError("bm25 index unavailable")

    monkeypatch.setattr(jobs_module.JobRepository, "search_keyword", _search_keyword)

    r = client.get(
        "/api/jobs/search/semantic",
        headers={"x-test-user": "u1"},
        params={"agent_id": "agent_mine", "query": "daily report"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "bm25 index unavailable" in body["error"]


# ── GET /search/keywords — job_retrieval_by_keywords semantics ─────────────


def test_search_keywords_denied_for_non_owner(client):
    r = client.get(
        "/api/jobs/search/keywords",
        headers={"x-test-user": "u1"},
        params={"agent_id": "agent_theirs", "keywords": ["news"]},
    )
    assert r.status_code == 403


def test_search_keywords_success_truncates_long_description(client, monkeypatch):
    async def _search_by_keywords(self, agent_id, keywords, user_id=None, status=None, limit=20):
        return [_fake_job(title="News digest", description="x" * 250)]

    monkeypatch.setattr(jobs_module.JobRepository, "search_by_keywords", _search_by_keywords)

    r = client.get(
        "/api/jobs/search/keywords",
        headers={"x-test-user": "u1"},
        params={"agent_id": "agent_mine", "keywords": ["news", "digest"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["total_results"] == 1
    assert body["jobs"][0]["title"] == "News digest"
    assert body["jobs"][0]["description"] == ("x" * 200) + "..."


def test_search_keywords_failure_shape(client, monkeypatch):
    async def _search_by_keywords(self, agent_id, keywords, user_id=None, status=None, limit=20):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(jobs_module.JobRepository, "search_by_keywords", _search_by_keywords)

    r = client.get(
        "/api/jobs/search/keywords",
        headers={"x-test-user": "u1"},
        params={"agent_id": "agent_mine", "keywords": ["news"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "db unavailable" in body["error"]


def test_update_job_type_switch_recomputes_next_run_with_new_type(client, monkeypatch):
    """Pre-open review #4: job_type is resolved BEFORE the trigger_config
    branch, so a one_off→scheduled switch submitted with a fresh cron computes
    next_run_time under the NEW type. Reading updates.get('job_type') inside
    the trigger branch (the old code) saw the old type, computed next_run as
    one_off → None, and zombified the job."""
    async def _get_job(self, job_id):
        return _fake_job(job_type=JobType.ONE_OFF)

    monkeypatch.setattr(jobs_module.JobRepository, "get_job", _get_job)

    seen_types = {}

    def _fake_compute(job_type, tc_model):
        seen_types["job_type"] = job_type
        # Only a scheduled type yields a next run for a cron config; one_off → None.
        if job_type == JobType.SCHEDULED:
            return SimpleNamespace(
                utc="2026-09-01T09:00:00+00:00", local="2026-09-01T09:00:00", tz="UTC"
            )
        return None

    monkeypatch.setattr(
        "narranexus.platform.utils.job_scheduling.compute_next_run",
        _fake_compute,
    )

    captured = {}

    async def _update_job(self, job_id, updates, agent_id=None):
        captured["updates"] = dict(updates)
        return {"success": True, "job_id": job_id, "updated_fields": list(updates.keys()), "message": "ok"}

    monkeypatch.setattr(job_service_module.JobInstanceService, "update_job", _update_job)

    r = client.put(
        "/api/jobs/job1",
        headers={"x-test-user": "u1"},
        json={
            "agent_id": "agent_mine",
            "job_type": "scheduled",
            "trigger_config": {"cron": "0 9 * * *", "timezone": "UTC"},
        },
    )
    assert r.status_code == 200
    # The fix: compute_next_run saw SCHEDULED (the new type), not ONE_OFF.
    assert seen_types["job_type"] == JobType.SCHEDULED
    assert captured["updates"]["next_run_time"] is not None
    assert captured["updates"]["job_type"] == JobType.SCHEDULED
