"""
@file_name: test_job_reschedule.py
@author: Bin Liang
@date: 2026-07-30
@description: User reschedule core logic (job_recovery.reschedule_job), called by
the authed dashboard route PUT /api/dashboard/jobs/{id}/schedule.

Covers the "edit execution time" feature: a user changes an existing job's
schedule rule (run_at for one_off; cron / interval_seconds for scheduled) plus
an optional timezone. The core merges the new time fields into trigger_config,
revalidates via TriggerConfig, recomputes next_run, and writes both atomically —
without changing the job's status (a paused job stays paused).
"""
import json
from datetime import datetime, timezone as dt_tz

import pytest

from xyz_agent_context.schema.job_schema import JobStatus
from xyz_agent_context.module.job_module.job_recovery import reschedule_job

SCHEDULED_CRON = '{"cron":"0 8 * * *","timezone":"Asia/Shanghai"}'
SCHEDULED_INTERVAL = '{"interval_seconds":3600,"timezone":"Asia/Shanghai"}'
ONE_OFF_RUN_AT = '{"run_at":"2026-08-01T09:00:00","timezone":"Asia/Shanghai"}'


async def _insert(db, job_id, status, job_type="scheduled", trigger=SCHEDULED_CRON,
                  failure_count=0):
    now = datetime(2026, 7, 30, tzinfo=dt_tz.utc).isoformat().replace("+00:00", "Z")
    await db.insert("instance_jobs", {
        "job_id": job_id, "instance_id": f"ins_{job_id}",
        "agent_id": "a", "user_id": "u", "title": "t", "description": "d", "payload": "p",
        "job_type": job_type, "trigger_config": trigger,
        "status": status, "notification_method": "inbox",
        "consecutive_failure_count": failure_count,
        "created_at": now, "updated_at": now,
    })


@pytest.mark.asyncio
async def test_reschedule_cron(db_client):
    await _insert(db_client, "job_r1", JobStatus.ACTIVE.value)
    ok, _ = await reschedule_job("job_r1", {"cron": "0 9 * * *"}, db_client)
    assert ok is True
    row = await db_client.get_one("instance_jobs", {"job_id": "job_r1"})
    assert '"cron": "0 9 * * *"' in row["trigger_config"]
    # next_run recomputed for the new cron in Asia/Shanghai → fires at 09:00 local
    assert row["next_run_at_local"].endswith("T09:00:00")
    assert row["next_run_tz"] == "Asia/Shanghai"


@pytest.mark.asyncio
async def test_reschedule_interval(db_client):
    await _insert(db_client, "job_r2", JobStatus.ACTIVE.value, trigger=SCHEDULED_INTERVAL)
    ok, _ = await reschedule_job("job_r2", {"interval_seconds": 7200}, db_client)
    assert ok is True
    row = await db_client.get_one("instance_jobs", {"job_id": "job_r2"})
    assert '"interval_seconds": 7200' in row["trigger_config"]
    assert row["next_run_at_local"] is not None


@pytest.mark.asyncio
async def test_reschedule_one_off_run_at(db_client):
    await _insert(db_client, "job_r3", JobStatus.PENDING.value,
                  job_type="one_off", trigger=ONE_OFF_RUN_AT)
    ok, _ = await reschedule_job("job_r3", {"run_at": "2026-08-02T10:00:00"}, db_client)
    assert ok is True
    row = await db_client.get_one("instance_jobs", {"job_id": "job_r3"})
    assert row["next_run_at_local"] == "2026-08-02T10:00:00"


@pytest.mark.asyncio
async def test_reschedule_timezone_only_preserves_cron(db_client):
    """exclude_none semantics: changing only the timezone must not drop cron."""
    await _insert(db_client, "job_r4", JobStatus.ACTIVE.value)
    ok, _ = await reschedule_job("job_r4", {"timezone": "America/New_York"}, db_client)
    assert ok is True
    row = await db_client.get_one("instance_jobs", {"job_id": "job_r4"})
    assert '"cron": "0 8 * * *"' in row["trigger_config"]
    assert row["next_run_tz"] == "America/New_York"


@pytest.mark.asyncio
async def test_switch_interval_to_cron_clears_interval(db_client):
    """Tier 1 mode switch: interval → cron must drop the stale interval_seconds
    so compute_next_run (which prefers cron) isn't shadowing a lingering field."""
    await _insert(db_client, "job_sw1", JobStatus.ACTIVE.value, trigger=SCHEDULED_INTERVAL)
    ok, _ = await reschedule_job("job_sw1", {"cron": "0 9 * * *"}, db_client)
    assert ok is True
    row = await db_client.get_one("instance_jobs", {"job_id": "job_sw1"})
    cfg = json.loads(row["trigger_config"])
    assert cfg["cron"] == "0 9 * * *"
    assert cfg["interval_seconds"] is None
    assert row["next_run_at_local"].endswith("T09:00:00")


@pytest.mark.asyncio
async def test_switch_cron_to_interval_clears_cron(db_client):
    await _insert(db_client, "job_sw2", JobStatus.ACTIVE.value, trigger=SCHEDULED_CRON)
    ok, _ = await reschedule_job("job_sw2", {"interval_seconds": 1800}, db_client)
    assert ok is True
    row = await db_client.get_one("instance_jobs", {"job_id": "job_sw2"})
    cfg = json.loads(row["trigger_config"])
    assert cfg["interval_seconds"] == 1800
    assert cfg["cron"] is None


@pytest.mark.asyncio
async def test_reschedule_paused_keeps_status(db_client):
    await _insert(db_client, "job_r5", JobStatus.PAUSED.value)
    ok, _ = await reschedule_job("job_r5", {"cron": "0 10 * * *"}, db_client)
    assert ok is True
    row = await db_client.get_one("instance_jobs", {"job_id": "job_r5"})
    assert row["status"] == JobStatus.PAUSED.value


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [
    JobStatus.RUNNING.value, JobStatus.COMPLETED.value,
    JobStatus.CANCELLED.value, JobStatus.FAILED.value,
])
async def test_cannot_reschedule_non_editable(db_client, status):
    await _insert(db_client, f"job_ne_{status}", status)
    ok, _ = await reschedule_job(f"job_ne_{status}", {"cron": "0 9 * * *"}, db_client)
    assert ok is False


@pytest.mark.asyncio
async def test_reschedule_invalid_timezone(db_client):
    await _insert(db_client, "job_r6", JobStatus.ACTIVE.value)
    ok, detail = await reschedule_job("job_r6", {"timezone": "Not/AZone"}, db_client)
    assert ok is False
    assert "invalid schedule" in detail


@pytest.mark.asyncio
async def test_reschedule_missing_time_field_rejected(db_client):
    """Guard: clearing the only fireable field for a scheduled job is rejected.

    The route strips None via exclude_none, so the UI can't hit this; the guard
    is defense-in-depth if the core is called directly with an explicit null.
    """
    await _insert(db_client, "job_r7", JobStatus.ACTIVE.value)
    ok, detail = await reschedule_job("job_r7", {"cron": None}, db_client)
    assert ok is False
    assert "cron or interval_seconds" in detail


@pytest.mark.asyncio
async def test_reschedule_job_not_found(db_client):
    ok, detail = await reschedule_job("nope", {"cron": "0 9 * * *"}, db_client)
    assert ok is False
    assert "not found" in detail


# ── Route: PUT /api/dashboard/jobs/{id}/schedule ────────────────────────────

import pytest_asyncio  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import backend.routes.dashboard.routes as routes_mod  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_get_db():
    import xyz_agent_context.utils.db.db_factory as db_factory_mod
    original = db_factory_mod.get_db_client
    yield
    db_factory_mod.get_db_client = original


def _build_client(db, viewer_id="u"):
    app = FastAPI()
    app.include_router(routes_mod.router, prefix="/api/dashboard")

    @app.middleware("http")
    async def _fake_auth(request, call_next):
        request.state.user_id = viewer_id
        return await call_next(request)

    async def _get_db_override():
        return db

    import xyz_agent_context.utils.db.db_factory as db_factory_mod
    db_factory_mod.get_db_client = _get_db_override
    return TestClient(app)


async def _seed_agent(db, agent_id="a", owner="u"):
    await db.insert("agents", {"agent_id": agent_id, "agent_name": "A", "created_by": owner})


@pytest.mark.asyncio
async def test_route_reschedule_success(db_client):
    await _seed_agent(db_client)
    await _insert(db_client, "job_rt1", JobStatus.ACTIVE.value)
    client = _build_client(db_client)
    r = client.put("/api/dashboard/jobs/job_rt1/schedule", json={"cron": "0 9 * * *"})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["next_run_at"].endswith("T09:00:00")


@pytest.mark.asyncio
async def test_route_reschedule_not_owned(db_client):
    await _seed_agent(db_client, owner="someone_else")
    await _insert(db_client, "job_rt2", JobStatus.ACTIVE.value)
    client = _build_client(db_client, viewer_id="u")
    r = client.put("/api/dashboard/jobs/job_rt2/schedule", json={"cron": "0 9 * * *"})
    # agent not visible to this viewer (private + not owner) → 404 masks existence
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_route_reschedule_job_not_found(db_client):
    client = _build_client(db_client)
    r = client.put("/api/dashboard/jobs/nope/schedule", json={"cron": "0 9 * * *"})
    assert r.status_code == 404
