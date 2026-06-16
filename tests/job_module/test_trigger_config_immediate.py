"""
@file_name: test_trigger_config_immediate.py
@author: Bin Liang
@date: 2026-06-01
@description: TriggerConfig.immediate() — canonical "fire now" one_off trigger.

Root-cause fix for the /api/jobs/complex bug: that endpoint hand-built
{"trigger_type": "immediate", "run_at": utc_now()} — three contract violations
at once (no such field as trigger_type; run_at was tz-AWARE, rejected by the
run_at_must_be_naive validator; no timezone). The fix gives callers ONE correct
way to say "now" so nobody hand-rolls an aware/timezone-less trigger again.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock

import pytest

from xyz_agent_context.schema.job_schema import TriggerConfig, JobType
from xyz_agent_context.module.job_module._job_scheduling import compute_next_run
from xyz_agent_context.module.job_module.job_service import JobInstanceService


def test_immediate_is_naive_utc_oneoff():
    t = TriggerConfig.immediate()
    assert t.run_at is not None
    assert t.run_at.tzinfo is None       # naive — satisfies run_at_must_be_naive
    assert t.timezone == "UTC"


def test_immediate_passes_validator_roundtrip():
    # model_dump(mode="json") is what the REST endpoint sends as a dict; it must
    # re-validate cleanly (run_at serialized as a naive ISO string, no offset/Z).
    d = TriggerConfig.immediate().model_dump(mode="json")
    assert "+" not in d["run_at"] and not d["run_at"].endswith("Z")
    TriggerConfig(**d)  # must not raise


def test_immediate_fires_approximately_now():
    nxt = compute_next_run(JobType.ONE_OFF, TriggerConfig.immediate())
    assert nxt is not None
    delta = abs((nxt.utc - datetime.now(timezone.utc)).total_seconds())
    assert delta < 60, f"immediate should fire ~now, off by {delta}s"


@pytest.mark.asyncio
async def test_create_oneoff_immediate_job_succeeds(db_client):
    """The exact path /api/jobs/complex needs: build an immediate one_off job."""
    service = JobInstanceService(db_client)
    result = await service.create_job_with_instance(
        agent_id="agent_1",
        user_id="user_1",
        title="run now",
        description="d",
        job_type="one_off",
        trigger_config=TriggerConfig.immediate().model_dump(mode="json"),
        payload="do it now",
    )
    assert result["success"], result
    row = await db_client.get_one("instance_jobs", {"job_id": result["job_id"]})
    assert row["next_run_time"] is not None
