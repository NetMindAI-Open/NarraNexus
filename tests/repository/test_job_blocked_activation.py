"""
@file_name: test_job_blocked_activation.py
@author: Bin Liang
@date: 2026-09-09
@description: B-16 — a Job whose ModuleInstance starts BLOCKED (has
dependencies) must itself be created with `JobStatus.BLOCKED`, and
`update_next_run_time_by_instance` (the method `JobModule.on_instance_activated`
calls when a dependency completes) must be able to flip it to ACTIVE.

Root cause: `JobRepository.create_job` hardcoded `status=JobStatus.PENDING`
regardless of the instance's initial status, and
`update_next_run_time_by_instance`'s `WHERE status IN (...)` never included
BLOCKED — so even a job that WAS correctly marked BLOCKED could never be
un-blocked by this method (0 rows affected), and a job that wasn't (the
pre-fix state) sat in `get_due_jobs()`'s PENDING/ACTIVE window from creation,
firing immediately regardless of its dependencies.
"""
from __future__ import annotations

from datetime import datetime, timezone as dt_tz

import pytest

from narranexus.platform.repository import JobRepository
from narranexus.platform.schema.job_schema import JobStatus, JobType, TriggerConfig


@pytest.mark.asyncio
async def test_create_job_with_blocked_status_persists_blocked(db_client):
    repo = JobRepository(db_client)
    await repo.create_job(
        agent_id="agent_1", user_id="user_1", job_id="job_blocked",
        title="t", description="d",
        job_type=JobType.ONE_OFF,
        trigger_config=TriggerConfig.immediate(),
        payload="p",
        instance_id="ins_blocked",
        status=JobStatus.BLOCKED,
    )

    row = await db_client.get_one("instance_jobs", {"job_id": "job_blocked"})
    assert row["status"] == "blocked"


@pytest.mark.asyncio
async def test_create_job_default_status_is_still_pending(db_client):
    """Existing callers that don't pass `status` must be unaffected."""
    repo = JobRepository(db_client)
    await repo.create_job(
        agent_id="agent_1", user_id="user_1", job_id="job_default",
        title="t", description="d",
        job_type=JobType.ONE_OFF,
        trigger_config=TriggerConfig.immediate(),
        payload="p",
        instance_id="ins_default",
    )

    row = await db_client.get_one("instance_jobs", {"job_id": "job_default"})
    assert row["status"] == "pending"


@pytest.mark.asyncio
async def test_update_next_run_time_by_instance_activates_a_blocked_job(db_client):
    repo = JobRepository(db_client)
    await repo.create_job(
        agent_id="agent_1", user_id="user_1", job_id="job_blocked_2",
        title="t", description="d",
        job_type=JobType.ONE_OFF,
        trigger_config=TriggerConfig.immediate(),
        payload="p",
        instance_id="ins_blocked_2",
        status=JobStatus.BLOCKED,
    )

    affected = await repo.update_next_run_time_by_instance(
        instance_id="ins_blocked_2",
        next_run_time=datetime(2026, 9, 9, 0, 0, 0, tzinfo=dt_tz.utc),
    )

    assert affected == 1
    row = await db_client.get_one("instance_jobs", {"job_id": "job_blocked_2"})
    assert row["status"] == "active"
    assert row["next_run_time"] is not None


@pytest.mark.asyncio
async def test_update_next_run_time_by_instance_still_works_for_pending_job(db_client):
    """Regression guard: the existing PENDING/ACTIVE activation path (used by
    every non-dependent job) must be unaffected."""
    repo = JobRepository(db_client)
    await repo.create_job(
        agent_id="agent_1", user_id="user_1", job_id="job_pending",
        title="t", description="d",
        job_type=JobType.ONE_OFF,
        trigger_config=TriggerConfig.immediate(),
        payload="p",
        instance_id="ins_pending",
    )

    affected = await repo.update_next_run_time_by_instance(
        instance_id="ins_pending",
        next_run_time=datetime(2026, 9, 9, 0, 0, 0, tzinfo=dt_tz.utc),
    )

    assert affected == 1
    row = await db_client.get_one("instance_jobs", {"job_id": "job_pending"})
    assert row["next_run_time"] is not None
