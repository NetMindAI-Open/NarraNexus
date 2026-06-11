"""
@file_name: test_job_service_create.py
@author: Bin Liang
@date: 2026-04-21
@description: Integration tests for JobInstanceService.create_job_with_instance
post-v2 (compute_next_run + beta fields populated on creation).
"""
from unittest.mock import patch, AsyncMock

import pytest

from xyz_agent_context.module.job_module.job_service import JobInstanceService


@pytest.mark.asyncio
async def test_create_job_populates_beta_fields(db_client):
    service = JobInstanceService(db_client)
    # Patch embedding paths to avoid external LLM calls
    result = await service.create_job_with_instance(
        agent_id="agent_1",
        user_id="user_1",
        title="Daily 8am reminder",
        description="d",
        job_type="scheduled",
        trigger_config={"cron": "0 8 * * *", "timezone": "Asia/Shanghai"},
        payload="Remind me",
    )
    assert result["success"], result
    row = await db_client.get_one("instance_jobs", {"job_id": result["job_id"]})
    assert row["next_run_tz"] == "Asia/Shanghai"
    assert row["next_run_at_local"] is not None
    assert row["next_run_time"] is not None


@pytest.mark.asyncio
async def test_create_jobs_batch_populates_beta(db_client):
    """create_jobs_batch delegates to create_job_with_instance; beta fields flow through."""
    service = JobInstanceService(db_client)
    result = await service.create_jobs_batch(
        agent_id="agent_1",
        user_id="user_1",
        jobs_config=[{
            "task_key": "k1",
            "title": "Batch A",
            "description": "d",
            "payload": "p",
            "job_type": "one_off",
            "trigger_config": {"run_at": "2026-05-01T08:00:00", "timezone": "Asia/Shanghai"},
        }],
    )
    assert result["success"], result
    assert len(result["created_jobs"]) == 1
    row = await db_client.get_one("instance_jobs", {"job_id": result["created_jobs"][0]})
    assert row["next_run_tz"] == "Asia/Shanghai"


@pytest.mark.asyncio
async def test_create_job_missing_timezone_returns_structured_error(db_client):
    service = JobInstanceService(db_client)
    result = await service.create_job_with_instance(
        agent_id="agent_1",
        user_id="user_1",
        title="bad",
        description="d",
        job_type="scheduled",
        trigger_config={"cron": "0 8 * * *"},  # missing timezone
        payload="p",
    )
    assert result["success"] is False
    assert "timezone" in result.get("error", "").lower()
    assert "Invalid trigger_config" in result["error"]
