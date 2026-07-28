"""
@file_name: test_manyfold_sync.py
@author: NexusAgent
@date: 2026-07-16
@description: Managed-trigger surface — control-message parsing, config
read endpoints, config-change webhook middleware, and execute_job_once
orchestration (JobTrigger execution body is stubbed; its own behavior is
covered by the job_module tests).
"""

import asyncio
import base64
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport

import backend.routes.manyfold.sync as mod
from xyz_agent_context.module.job_module.job_trigger import JobTrigger
from xyz_agent_context.repository.job_repository import JobRepository


# ---------------------------------------------------------------------------
# parse_run_job_control — strict full-match only
# ---------------------------------------------------------------------------


def test_parse_run_job_control_matches_exact_control_message():
    assert mod.parse_run_job_control("[[nx:run_job job_abc-123 v1]]") == "job_abc-123"
    assert mod.parse_run_job_control("  [[nx:run_job j1 v1]]  ") == "j1"


def test_parse_run_job_control_rejects_everything_else():
    assert mod.parse_run_job_control("") is None
    assert mod.parse_run_job_control("hello") is None
    # Surrounding text = a normal chat turn that merely mentions the syntax.
    assert mod.parse_run_job_control("run [[nx:run_job j1 v1]]") is None
    assert mod.parse_run_job_control("[[nx:run_job j1 v1]] please") is None
    # Unknown version / malformed id must fall through to a normal run.
    assert mod.parse_run_job_control("[[nx:run_job j1 v2]]") is None
    assert mod.parse_run_job_control("[[nx:run_job bad id v1]]") is None


# ---------------------------------------------------------------------------
# _classify_config_path — which routes count as config writes
# ---------------------------------------------------------------------------


def test_classify_config_path():
    assert mod._classify_config_path("/api/jobs") == "jobs"
    assert mod._classify_config_path("/api/jobs/complex") == "jobs"
    # Provider mutations edge-trigger PAUSED_NO_QUOTA resume → job state.
    assert mod._classify_config_path("/api/providers") == "jobs"
    for p in ("lark", "slack", "telegram", "wechat", "discord", "narramessenger"):
        assert mod._classify_config_path(f"/api/{p}/bind") == "channels"
    assert mod._classify_config_path("/api/agents") is None
    assert mod._classify_config_path("/api/jobsx") is None


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------


def _make_app(db_client, monkeypatch, *, authed: bool) -> FastAPI:
    async def _fake_db():
        return db_client

    monkeypatch.setattr(mod, "get_db_client", _fake_db)
    app = FastAPI()

    if authed:
        @app.middleware("http")
        async def _fake_auth(request: Request, call_next):
            request.state.manyfold_authed = True
            return await call_next(request)

    app.include_router(mod.router)
    return app


async def _get(app: FastAPI, path: str) -> httpx.Response:
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
        return await ac.get(path)


@pytest.mark.asyncio
async def test_jobs_endpoint_requires_gateway_auth(db_client, monkeypatch):
    app = _make_app(db_client, monkeypatch, authed=False)
    resp = await _get(app, "/manyfold/jobs")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_jobs_endpoint_excludes_terminal_jobs(db_client, monkeypatch):
    for job_id, status in (
        ("j_active", "active"),
        ("j_cooling", "cooling"),
        ("j_done", "completed"),
        ("j_cancelled", "cancelled"),
        ("j_failed", "failed"),
    ):
        await db_client.insert(
            "instance_jobs",
            {
                "job_id": job_id,
                "instance_id": f"inst_{job_id}",
                "agent_id": "agent_1",
                "user_id": "u1",
                "title": f"t {job_id}",
                "status": status,
                "job_type": "scheduled",
                "next_run_time": "2026-07-16T12:00:00+00:00",
            },
        )
    app = _make_app(db_client, monkeypatch, authed=True)
    resp = await _get(app, "/manyfold/jobs")
    assert resp.status_code == 200
    body = resp.json()
    ids = {row["job_id"] for row in body["data"]}
    assert ids == {"j_active", "j_cooling"}
    row = next(r for r in body["data"] if r["job_id"] == "j_active")
    assert row["agent_id"] == "agent_1"
    assert row["status"] == "active"
    assert row["next_run_time"].startswith("2026-07-16T12:00:00")


@pytest.mark.asyncio
async def test_channels_endpoint_decodes_telegram_binding(db_client, monkeypatch):
    await db_client.insert(
        "channel_telegram_credentials",
        {
            "agent_id": "agent_1",
            "bot_token_encoded": base64.b64encode(
                b"123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            ).decode(),
            "bot_user_id": "42",
            "bot_username": "nx_bot",
            "enabled": 1,
        },
    )
    app = _make_app(db_client, monkeypatch, authed=True)
    resp = await _get(app, "/manyfold/channels")
    assert resp.status_code == 200
    rows = resp.json()["data"]
    tg = [r for r in rows if r["provider"] == "telegram"]
    assert len(tg) == 1
    assert tg[0]["agent_id"] == "agent_1"
    assert tg[0]["external_id"] == "42"
    assert tg[0]["credentials"]["bot_token"] == "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    assert tg[0]["config"]["bot_username"] == "nx_bot"


# ---------------------------------------------------------------------------
# Config-change webhook middleware
# ---------------------------------------------------------------------------


def _make_middleware_app(monkeypatch, fired: list) -> FastAPI:
    monkeypatch.setattr(
        mod, "notify_manyfold_config_changed", lambda kinds: fired.append(kinds)
    )
    app = FastAPI()
    app.middleware("http")(mod.config_change_webhook_middleware)

    @app.post("/api/jobs/complex")
    async def _jobs():
        return {"ok": True}

    @app.get("/api/jobs")
    async def _jobs_list():
        return {"ok": True}

    @app.post("/api/telegram/bind")
    async def _bind():
        return {"ok": True}

    @app.post("/api/agents")
    async def _agents():
        return {"ok": True}

    @app.post("/api/jobs/fail")
    async def _fail():
        raise ValueError("boom")

    return app


@pytest.mark.asyncio
async def test_middleware_fires_on_successful_config_writes(monkeypatch):
    fired: list = []
    app = _make_middleware_app(monkeypatch, fired)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
        await ac.post("/api/jobs/complex")
        await ac.post("/api/telegram/bind")
    assert fired == [{"jobs"}, {"channels"}]


@pytest.mark.asyncio
async def test_middleware_ignores_reads_other_paths_and_errors(monkeypatch):
    fired: list = []
    app = _make_middleware_app(monkeypatch, fired)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
        await ac.get("/api/jobs")
        await ac.post("/api/agents")
        with pytest.raises(ValueError):
            await ac.post("/api/jobs/fail")
    assert fired == []


# ---------------------------------------------------------------------------
# notify_manyfold_config_changed — coalescing + never raises
# ---------------------------------------------------------------------------


class _FakeAsyncClient:
    posts: list = []
    fail = False

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        _FakeAsyncClient.posts.append({"url": url, "json": json, "headers": headers})
        if _FakeAsyncClient.fail:
            raise httpx.ConnectError("down")
        return SimpleNamespace(raise_for_status=lambda: None)


@pytest.fixture
def webhook_env(monkeypatch):
    monkeypatch.setenv("MANYFOLD_SYNC_WEBHOOK_URL", "http://mf/notify")
    monkeypatch.setenv("MANYFOLD_SYNC_WEBHOOK_TOKEN", "tok")
    monkeypatch.setenv("MANYFOLD_RUNTIME_ID", "rt_1")
    monkeypatch.setattr(mod.httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.posts = []
    _FakeAsyncClient.fail = False
    mod._pending_kinds.clear()


@pytest.mark.asyncio
async def test_notify_coalesces_bursts_into_one_post(webhook_env):
    mod.notify_manyfold_config_changed({"jobs"})
    mod.notify_manyfold_config_changed({"channels"})
    await asyncio.sleep(0.7)
    assert len(_FakeAsyncClient.posts) == 1
    post = _FakeAsyncClient.posts[0]
    assert post["json"] == {"runtimeId": "rt_1", "kinds": ["channels", "jobs"]}
    assert post["headers"]["Authorization"] == "Bearer tok"


@pytest.mark.asyncio
async def test_notify_failure_never_raises(webhook_env):
    _FakeAsyncClient.fail = True
    mod.notify_manyfold_config_changed({"jobs"})
    await asyncio.sleep(0.7)
    assert len(_FakeAsyncClient.posts) == 1


@pytest.mark.asyncio
async def test_notify_noops_without_env(monkeypatch):
    monkeypatch.delenv("MANYFOLD_SYNC_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(mod.httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.posts = []
    mod.notify_manyfold_config_changed({"jobs"})
    await asyncio.sleep(0.7)
    assert _FakeAsyncClient.posts == []


# ---------------------------------------------------------------------------
# execute_job_once — orchestration around the (stubbed) JobTrigger body
# ---------------------------------------------------------------------------


def _fake_job(job_id="j1", agent_id="agent_1", status="active"):
    return SimpleNamespace(job_id=job_id, agent_id=agent_id, status=status)


@pytest.fixture
def job_stubs(db_client, monkeypatch):
    """Stub the heavy JobTrigger pieces; keep execute_job_once's own logic
    (maintenance ordering, status gates, drain accounting) real."""
    state = {
        "job": None,
        "executed": [],
        "due": [],
        "rearmed": 0,
        "resumed": 0,
    }

    async def _fake_db():
        return db_client

    monkeypatch.setattr(mod, "get_db_client", _fake_db)

    async def _get_job(self, job_id):
        job = state["job"]
        return job if job is not None and job.job_id == job_id else None

    async def _get_due(self, limit=100):
        return list(state["due"])

    async def _execute(self, job):
        state["executed"].append(job.job_id)
        state["due"] = [j for j in state["due"] if j.job_id != job.job_id]

    async def _rearm(self):
        state["rearmed"] += 1
        return 0

    async def _resume(self):
        state["resumed"] += 1
        return 0

    monkeypatch.setattr(JobRepository, "get_job", _get_job)
    monkeypatch.setattr(JobRepository, "get_due_jobs", _get_due)
    monkeypatch.setattr(JobTrigger, "_execute_job", _execute)
    monkeypatch.setattr(JobTrigger, "_rearm_cooled_jobs", _rearm)
    monkeypatch.setattr(JobTrigger, "_resume_eligible_no_quota_jobs", _resume)
    # Collapse the drain window so the no-more-due exit is immediate.
    monkeypatch.setattr(mod, "_DRAIN_WINDOW_S", 0)
    monkeypatch.setattr(mod, "_DRAIN_POLL_INTERVAL_S", 0)
    return state


@pytest.mark.asyncio
async def test_execute_job_once_runs_job_and_reports_status(job_stubs):
    job_stubs["job"] = _fake_job()
    outcome = await mod.execute_job_once("agent_1", "j1")
    assert outcome.ok is True
    assert job_stubs["executed"] == ["j1"]
    # Maintenance passes ran before execution (poller is off in managed mode).
    assert job_stubs["rearmed"] == 1
    assert job_stubs["resumed"] == 1
    assert outcome.as_text().startswith("nx:run_job j1 ok status=active")


@pytest.mark.asyncio
async def test_execute_job_once_skips(job_stubs):
    outcome = await mod.execute_job_once("agent_1", "j1")
    assert (outcome.ok, outcome.reason) == (False, "not_found")

    job_stubs["job"] = _fake_job(agent_id="other_agent")
    outcome = await mod.execute_job_once("agent_1", "j1")
    assert (outcome.ok, outcome.reason) == (False, "wrong_agent")

    for status, reason in (
        ("running", "already_running"),
        ("completed", "terminal"),
        ("paused", "status_paused"),
        ("blocked_failed", "status_blocked_failed"),
    ):
        job_stubs["job"] = _fake_job(status=status)
        outcome = await mod.execute_job_once("agent_1", "j1")
        assert (outcome.ok, outcome.reason) == (False, reason)
        assert job_stubs["executed"] == []


@pytest.mark.asyncio
async def test_execute_job_once_drains_due_jobs_up_to_cap(job_stubs, monkeypatch):
    monkeypatch.setattr(mod, "_DRAIN_WINDOW_S", 5)
    job_stubs["job"] = _fake_job()
    job_stubs["due"] = [
        _fake_job(job_id=f"dep_{i}") for i in range(mod._DRAIN_LIMIT + 2)
    ]
    outcome = await mod.execute_job_once("agent_1", "j1")
    assert outcome.ok is True
    assert outcome.drained == mod._DRAIN_LIMIT
    assert job_stubs["executed"][0] == "j1"
    assert len(job_stubs["executed"]) == 1 + mod._DRAIN_LIMIT


@pytest.mark.asyncio
async def test_execute_job_once_never_raises(job_stubs, monkeypatch):
    async def _boom(self, job_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(JobRepository, "get_job", _boom)
    job_stubs["job"] = _fake_job()
    outcome = await mod.execute_job_once("agent_1", "j1")
    assert (outcome.ok, outcome.reason) == (False, "internal_error")
