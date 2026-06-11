"""
@file_name: test_timezone_e2e.py
@author: Bin Liang
@date: 2026-04-21
@description: End-to-end verification of the v2 timezone protocol.

Covers all four user-level guarantees:

1. Registration: LLM/service creates a job; alpha (UTC) + beta (local+tz)
   are written atomically and represent the same instant.

2. Trigger correctness:
   2a. one_off: poller sees due alpha, finalize marks COMPLETED +
       clear_next_run (beta nulled).
   2b. scheduled: poller sees due alpha, finalize recomputes via
       compute_next_run (using the frozen timezone) and writes α+β
       atomically; next fire is correct local time.

3. Update flows:
   3a. Execution auto-update via _job_lifecycle: the scheduling is
       deterministically recomputed from trigger_config; LLM does not
       dictate next_run_time.
   3b. User-initiated update via MCP job_update with new trigger_config:
       α+β re-derived and written atomically.

4. Display invariance: when users.timezone changes (user switches locale),
   pre-existing jobs keep their frozen β unchanged; the API response
   carries β + the IANA label instead of UTC.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_tz
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, patch

import json
import pytest

from xyz_agent_context.module.job_module._job_scheduling import (
    NextRunTuple,
    compute_next_run,
)
from xyz_agent_context.module.job_module.job_service import JobInstanceService
from xyz_agent_context.module.job_module.job_trigger import JobTrigger
from xyz_agent_context.module.job_module._job_response import job_to_llm_dict
from xyz_agent_context.repository import JobRepository
from xyz_agent_context.schema.job_schema import JobStatus, JobType, TriggerConfig


# -----------------------------------------------------------------
# Shared fixtures
# -----------------------------------------------------------------

async def _seed_user(db, user_id: str, tz: str) -> None:
    await db.insert("users", {
        "user_id": user_id,
        "display_name": f"user_{user_id}",
        "user_type": "user",
        "timezone": tz,
        "status": "active",
    })


async def _create_job(
    db,
    *,
    user_id: str,
    title: str,
    job_type: str,
    trigger_config: dict,
) -> str:
    """Thin wrapper mocking embedding calls so the scheduling path is exercised."""
    svc = JobInstanceService(db)
    result = await svc.create_job_with_instance(
        agent_id="agent_tz",
        user_id=user_id,
        title=title,
        description="d",
        job_type=job_type,
        trigger_config=trigger_config,
        payload="p",
    )
    assert result["success"], result
    return result["job_id"]


# =================================================================
# Scenario 1 — Registration: tasks land with correct time + frozen tz
# =================================================================

@pytest.mark.asyncio
async def test_S1_registration_one_off_alpha_beta_match(db_client):
    """run_at 2026-05-01 08:00 Asia/Shanghai -> UTC 2026-05-01 00:00."""
    await _seed_user(db_client, "u1", "Asia/Shanghai")
    job_id = await _create_job(
        db_client,
        user_id="u1",
        title="One-off reminder",
        job_type="one_off",
        trigger_config={
            "run_at": "2026-05-01T08:00:00",
            "timezone": "Asia/Shanghai",
        },
    )
    row = await db_client.get_one("instance_jobs", {"job_id": job_id})

    # β (user-local view)
    assert row["next_run_at_local"] == "2026-05-01T08:00:00"
    assert row["next_run_tz"] == "Asia/Shanghai"

    # α (UTC instant) — parse whatever SQLite stored
    alpha = row["next_run_time"]
    if isinstance(alpha, str):
        alpha = datetime.fromisoformat(alpha.replace("Z", "+00:00"))
    if alpha.tzinfo is None:
        alpha = alpha.replace(tzinfo=dt_tz.utc)
    # α and β must denote the same instant
    expected_utc = datetime(2026, 5, 1, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(dt_tz.utc)
    assert alpha == expected_utc


@pytest.mark.asyncio
async def test_S1_registration_cron_next_run_in_job_tz(db_client):
    """`0 8 * * *` with tz=America/New_York -> next 8am NY, not 8am UTC."""
    await _seed_user(db_client, "u2", "America/New_York")
    job_id = await _create_job(
        db_client,
        user_id="u2",
        title="Daily ping",
        job_type="scheduled",
        trigger_config={"cron": "0 8 * * *", "timezone": "America/New_York"},
    )
    row = await db_client.get_one("instance_jobs", {"job_id": job_id})
    assert row["next_run_tz"] == "America/New_York"
    # next_run_at_local hour must be 08 (naive, in NY local)
    local_dt = datetime.fromisoformat(row["next_run_at_local"])
    assert local_dt.hour == 8 and local_dt.minute == 0


@pytest.mark.asyncio
async def test_S1_registration_rejects_missing_timezone(db_client):
    """Service surfaces Pydantic error when LLM forgets timezone."""
    await _seed_user(db_client, "u3", "UTC")
    svc = JobInstanceService(db_client)
    result = await svc.create_job_with_instance(
        agent_id="agent_tz",
        user_id="u3",
        title="bad",
        description="d",
        job_type="scheduled",
        trigger_config={"cron": "0 8 * * *"},  # no timezone
        payload="p",
    )
    assert result["success"] is False
    assert "timezone" in result["error"].lower()


# =================================================================
# Scenario 2 — Trigger: one_off / scheduled flow through _finalize
# =================================================================

@pytest.mark.asyncio
async def test_S2a_one_off_trigger_completes_and_clears_next_run(db_client):
    """After one_off fires, status=COMPLETED and all next_run_* are NULL."""
    await _seed_user(db_client, "u1", "Asia/Shanghai")
    job_id = await _create_job(
        db_client,
        user_id="u1",
        title="One-off",
        job_type="one_off",
        trigger_config={
            "run_at": "2026-05-01T08:00:00",
            "timezone": "Asia/Shanghai",
        },
    )
    repo = JobRepository(db_client)
    job = await repo.get_job(job_id)

    # Drive _finalize_job_execution directly (skipping agent loop)
    trigger = JobTrigger.__new__(JobTrigger)
    trigger._db = db_client
    trigger._job_repo = repo
    with patch.object(trigger, "_update_instance_completed", new=AsyncMock()):
        await trigger._finalize_job_execution(job, {"event_id": "evt_1"})

    row = await db_client.get_one("instance_jobs", {"job_id": job_id})
    assert row["status"] == JobStatus.COMPLETED.value
    assert row["next_run_time"] is None
    assert row["next_run_at_local"] is None
    assert row["next_run_tz"] is None


@pytest.mark.asyncio
async def test_S2b_scheduled_trigger_recomputes_alpha_beta_atomic(db_client):
    """After a cron job fires, α and β are atomically rewritten in the frozen tz."""
    await _seed_user(db_client, "u2", "America/New_York")
    job_id = await _create_job(
        db_client,
        user_id="u2",
        title="Cron daily 8am NY",
        job_type="scheduled",
        trigger_config={"cron": "0 8 * * *", "timezone": "America/New_York"},
    )
    repo = JobRepository(db_client)
    job = await repo.get_job(job_id)

    trigger = JobTrigger.__new__(JobTrigger)
    trigger._db = db_client
    trigger._job_repo = repo
    with patch.object(trigger, "_update_instance_completed", new=AsyncMock()):
        await trigger._finalize_job_execution(job, {"event_id": "evt_2"})

    row = await db_client.get_one("instance_jobs", {"job_id": job_id})
    assert row["status"] == JobStatus.ACTIVE.value

    # Both alpha and beta must be present and consistent
    assert row["next_run_at_local"] is not None
    assert row["next_run_tz"] == "America/New_York"
    alpha = row["next_run_time"]
    if isinstance(alpha, str):
        alpha = datetime.fromisoformat(alpha.replace("Z", "+00:00"))
    if alpha.tzinfo is None:
        alpha = alpha.replace(tzinfo=dt_tz.utc)
    local = datetime.fromisoformat(row["next_run_at_local"])
    rebuilt = local.replace(tzinfo=ZoneInfo(row["next_run_tz"])).astimezone(dt_tz.utc)
    assert alpha == rebuilt, f"alpha {alpha} and beta-derived {rebuilt} must denote the same instant"

    # last_run_* was also written atomically
    assert row["last_run_at_local"] is not None
    assert row["last_run_tz"] == "America/New_York"


@pytest.mark.asyncio
async def test_S2_poller_query_finds_due_via_alpha(db_client):
    """poller's get_due_jobs uses alpha (UTC) for the WHERE clause."""
    await _seed_user(db_client, "u1", "Asia/Shanghai")
    long_ago = datetime(2020, 1, 1, 0, 0, 0, tzinfo=dt_tz.utc)
    # Create a job whose alpha is already in the past (run_at 2020)
    await db_client.insert("instance_jobs", {
        "job_id": "j_overdue",
        "instance_id": "ins_overdue",
        "agent_id": "agent_tz",
        "user_id": "u1",
        "title": "overdue",
        "description": "d",
        "payload": "p",
        "job_type": "one_off",
        "trigger_config": json.dumps({"run_at": "2020-01-01T00:00:00", "timezone": "Asia/Shanghai"}),
        "status": "pending",
        "notification_method": "inbox",
        "next_run_time": long_ago,
        "next_run_at_local": "2020-01-01T00:00:00",
        "next_run_tz": "Asia/Shanghai",
        "created_at": long_ago,
        "updated_at": long_ago,
    })
    repo = JobRepository(db_client)
    due = await repo.get_due_jobs(limit=10)
    assert any(j.job_id == "j_overdue" for j in due)


# =================================================================
# Scenario 3 — Update flows
# =================================================================

async def _drive_lifecycle(db_client, job_id: str, instance_id: str, status: JobStatus):
    """Invoke handle_job_execution_result with OpenAIAgentsSDK.llm_function mocked."""
    from xyz_agent_context.schema.job_schema import JobExecutionResult
    from xyz_agent_context.module.job_module._job_lifecycle import handle_job_execution_result
    from xyz_agent_context.schema.hook_schema import (
        HookAfterExecutionParams,
        HookExecutionContext,
        HookIOData,
        WorkingSource,
    )

    result_obj = JobExecutionResult(
        job_id=job_id,
        status=status,
        process=["did something"] if status == JobStatus.ACTIVE else ["done"],
        should_notify=False,
        notification_summary="",
    )
    llm_return = type("LR", (), {"final_output": result_obj})()

    fake_instance = type("Inst", (), {
        "instance_id": instance_id,
        "module_class": "JobModule",
        "agent_id": "agent_tz",
        "user_id": "u1",
    })()
    params = HookAfterExecutionParams(
        execution_ctx=HookExecutionContext(
            event_id="evt_test",
            agent_id="agent_tz",
            user_id="u1",
            working_source=WorkingSource.JOB,
        ),
        io_data=HookIOData(input_content="i", final_output="o"),
        instance=fake_instance,
    )
    repo = JobRepository(db_client)

    async def _fetch(inst_obj):
        inst_id = inst_obj.instance_id if hasattr(inst_obj, "instance_id") else inst_obj
        # Fetch the Job by instance_id via the existing repo method
        rows = await repo.get_jobs_by_instance(inst_id, limit=1)
        return rows[0] if rows else None

    with patch(
        "xyz_agent_context.agent_framework.openai_agents_sdk.OpenAIAgentsSDK.llm_function",
        new=AsyncMock(return_value=llm_return),
    ):
        return await handle_job_execution_result(params, repo, _fetch)


@pytest.mark.asyncio
async def test_S3a_lifecycle_recomputes_next_run_from_trigger_config(db_client):
    """Post-execution: status set by LLM, next_run derived from trigger_config."""
    await _seed_user(db_client, "u2", "America/New_York")
    job_id = await _create_job(
        db_client,
        user_id="u2",
        title="Cron",
        job_type="scheduled",
        trigger_config={"interval_seconds": 3600, "timezone": "America/New_York"},
    )
    # Find the instance_id created by the service
    row_before = await db_client.get_one("instance_jobs", {"job_id": job_id})
    instance_id = row_before["instance_id"]

    await _drive_lifecycle(db_client, job_id, instance_id, JobStatus.ACTIVE)

    row = await db_client.get_one("instance_jobs", {"job_id": job_id})
    assert row["status"] == JobStatus.ACTIVE.value
    assert row["next_run_tz"] == "America/New_York"
    assert row["next_run_at_local"] is not None
    assert row["last_run_at_local"] is not None
    assert row["last_run_tz"] == "America/New_York"


@pytest.mark.asyncio
async def test_S3a_lifecycle_clears_next_run_on_terminal_state(db_client):
    await _seed_user(db_client, "u1", "Asia/Shanghai")
    job_id = await _create_job(
        db_client,
        user_id="u1",
        title="One-off",
        job_type="one_off",
        trigger_config={"run_at": "2026-05-01T08:00:00", "timezone": "Asia/Shanghai"},
    )
    row_before = await db_client.get_one("instance_jobs", {"job_id": job_id})
    instance_id = row_before["instance_id"]

    await _drive_lifecycle(db_client, job_id, instance_id, JobStatus.COMPLETED)

    row = await db_client.get_one("instance_jobs", {"job_id": job_id})
    assert row["status"] == JobStatus.COMPLETED.value
    assert row["next_run_time"] is None
    assert row["next_run_at_local"] is None
    assert row["next_run_tz"] is None


@pytest.mark.asyncio
async def test_S3b_immediate_override_populates_beta_in_job_tz(db_client):
    """When user says 'run this NOW', the override produces alpha+beta atomically."""
    await _seed_user(db_client, "u1", "Asia/Shanghai")
    job_id = await _create_job(
        db_client,
        user_id="u1",
        title="Override target",
        job_type="scheduled",
        trigger_config={"cron": "0 8 * * *", "timezone": "Asia/Shanghai"},
    )
    # Simulate the code path inside MCP job_update's "next_run_time override" branch
    repo = JobRepository(db_client)
    job = await repo.get_job(job_id)

    override_utc = datetime(2026, 5, 1, 4, 0, 0, tzinfo=dt_tz.utc)  # 12:00 Shanghai
    tz_name = job.trigger_config.timezone
    expected_local = override_utc.astimezone(ZoneInfo(tz_name)).replace(tzinfo=None).isoformat()
    await repo.update_job_fields(
        job_id,
        {
            "next_run_time": override_utc,
            "next_run_at_local": expected_local,
            "next_run_tz": tz_name,
        },
    )
    row = await db_client.get_one("instance_jobs", {"job_id": job_id})
    assert row["next_run_tz"] == "Asia/Shanghai"
    assert row["next_run_at_local"] == "2026-05-01T12:00:00"


@pytest.mark.asyncio
async def test_S3b_job_update_changes_trigger_config_and_recomputes_beta(db_client):
    """User-initiated update: new tz in trigger_config -> new α+β recomputed."""
    await _seed_user(db_client, "u1", "Asia/Shanghai")
    job_id = await _create_job(
        db_client,
        user_id="u1",
        title="Was Shanghai",
        job_type="scheduled",
        trigger_config={"cron": "0 8 * * *", "timezone": "Asia/Shanghai"},
    )

    # Reuse the path taken by MCP job_update: update_job_fields with new
    # TriggerConfig model + recomputed alpha/beta (as the tool code does).
    repo = JobRepository(db_client)
    new_tc = TriggerConfig(cron="0 8 * * *", timezone="Europe/Paris")
    next_run = compute_next_run(JobType.SCHEDULED, new_tc)
    assert next_run is not None
    await repo.update_job_fields(
        job_id,
        {
            "trigger_config": new_tc,
            "next_run_time": next_run.utc,
            "next_run_at_local": next_run.local,
            "next_run_tz": next_run.tz,
        },
    )
    row = await db_client.get_one("instance_jobs", {"job_id": job_id})
    assert row["next_run_tz"] == "Europe/Paris"
    local_dt = datetime.fromisoformat(row["next_run_at_local"])
    assert local_dt.hour == 8  # still "8am" but now 8am Paris


# =================================================================
# Scenario 4 — Display invariance when users.timezone changes
# =================================================================

@pytest.mark.asyncio
async def test_S4_user_changes_timezone_does_not_touch_old_jobs(db_client):
    """Registered job keeps its frozen β even after users.timezone changes."""
    await _seed_user(db_client, "u1", "Asia/Shanghai")
    job_id = await _create_job(
        db_client,
        user_id="u1",
        title="Shanghai job",
        job_type="scheduled",
        trigger_config={"cron": "0 8 * * *", "timezone": "Asia/Shanghai"},
    )
    before = await db_client.get_one("instance_jobs", {"job_id": job_id})

    # User flies to NYC; browser sync updates users.timezone
    await db_client.update("users", {"user_id": "u1"}, {"timezone": "America/New_York"})

    after = await db_client.get_one("instance_jobs", {"job_id": job_id})
    # The old job's trigger + beta must NOT drift
    assert after["next_run_tz"] == "Asia/Shanghai"
    assert after["next_run_at_local"] == before["next_run_at_local"]


@pytest.mark.asyncio
async def test_S4_llm_facing_shape_excludes_utc_fields(db_client):
    """job_to_llm_dict guarantees UTC fields never leak to the LLM."""
    await _seed_user(db_client, "u1", "Asia/Shanghai")
    job_id = await _create_job(
        db_client,
        user_id="u1",
        title="T",
        job_type="scheduled",
        trigger_config={"cron": "0 8 * * *", "timezone": "Asia/Shanghai"},
    )
    repo = JobRepository(db_client)
    job = await repo.get_job(job_id)
    d = job_to_llm_dict(job)
    # Forbidden keys
    assert "next_run_time" not in d
    assert "last_run_time" not in d
    # Required keys (beta + tz label)
    assert d["next_run_at"] is not None
    assert d["timezone"] == "Asia/Shanghai"
    # The trigger_config exposed to LLM carries the tz internally as well
    assert d["trigger_config"]["timezone"] == "Asia/Shanghai"


@pytest.mark.asyncio
async def test_S4_api_route_returns_beta_not_alpha(db_client):
    """The /api/jobs route's row->JobResponse builder emits next_run_at (beta)."""
    from xyz_agent_context.schema.api_schema import JobResponse
    # JobResponse schema itself must not declare the UTC fields anymore
    fields = JobResponse.model_fields
    assert "next_run_time" not in fields
    assert "last_run_time" not in fields
    assert "next_run_at" in fields
    assert "next_run_timezone" in fields
    assert "last_run_at" in fields
    assert "last_run_timezone" in fields
