"""
@file_name: test_job_similar_title_dedup.py
@author: Bin Liang
@date: 2026-08-04
@description: Similar-title dedup must confirm, not silently swallow (W1).

Live-reproduced 2026-08-04: 'Daily Weather Report CL' (Beijing 8am) was
silently merged into 'Daily Weather Report DS' (Shenzhen 7am) — Jaccard 0.6
over a 0.5 threshold, different content, the user's intent dropped with
success=True. And the candidate pool was per-agent, so ANOTHER user's job
title could block yours. The contract now:

- exact same title, same user → is_existing (idempotency, unchanged)
- similar title, same user → success=False + needs_confirmation + the
  similar job's summary, so the model can ask the user instead of lying
- similar title, DIFFERENT user → creates (no cross-user coupling)
- confirm_new=True → creates (the user said they want a new one)
"""
import pytest

from xyz_agent_context.module.job_module.job_service import JobInstanceService

BASE = dict(
    description="d",
    job_type="one_off",
    trigger_config={"run_at": "2026-09-01T08:00:00", "timezone": "Asia/Shanghai"},
    payload="p",
)


async def _create(service, **kw):
    return await service.create_job_with_instance(**{**BASE, **kw})


@pytest.mark.asyncio
async def test_similar_title_same_user_asks_for_confirmation(db_client):
    service = JobInstanceService(db_client)
    first = await _create(service, agent_id="agent_1", user_id="user_1",
                          title="Daily Weather Report DS")
    assert first["success"], first

    second = await _create(service, agent_id="agent_1", user_id="user_1",
                           title="Daily News Report DS")
    assert second["success"] is False
    assert second["needs_confirmation"] is True
    assert second["similar_job"]["job_id"] == first["job_id"]
    assert second["similar_job"]["title"] == "Daily Weather Report DS"
    assert "confirm_new" in second["error"]

    rows = await db_client.get("instance_jobs", filters={"agent_id": "agent_1"})
    assert len(rows) == 1  # nothing was silently created OR merged


@pytest.mark.asyncio
async def test_similar_title_different_user_is_not_blocked(db_client):
    service = JobInstanceService(db_client)
    first = await _create(service, agent_id="agent_1", user_id="user_1",
                          title="Daily Weather Report DS")
    assert first["success"], first

    second = await _create(service, agent_id="agent_1", user_id="user_2",
                           title="Daily News Report DS")
    assert second["success"] is True, second
    assert not second.get("is_existing")


@pytest.mark.asyncio
async def test_empty_user_id_still_has_its_own_candidate_pool(db_client):
    """An empty user ID is a value, not a request for agent-wide candidates."""
    service = JobInstanceService(db_client)
    first = await _create(service, agent_id="agent_1", user_id="user_1",
                          title="Daily Weather Report DS")
    assert first["success"], first

    second = await _create(service, agent_id="agent_1", user_id="",
                           title="Daily News Report DS")
    assert second["success"] is True, second
    assert not second.get("is_existing")


@pytest.mark.asyncio
async def test_confirm_new_creates_despite_similarity(db_client):
    service = JobInstanceService(db_client)
    first = await _create(service, agent_id="agent_1", user_id="user_1",
                          title="Daily Weather Report DS")
    assert first["success"], first

    second = await _create(service, agent_id="agent_1", user_id="user_1",
                           title="Daily News Report DS", confirm_new=True)
    assert second["success"] is True, second
    assert not second.get("is_existing")
    assert second["job_id"] != first["job_id"]


@pytest.mark.asyncio
async def test_exact_title_same_user_stays_idempotent(db_client):
    """confirm_new bypasses the SIMILARITY gate only — an exact re-creation
    of the same title is still the repeat-call protection."""
    service = JobInstanceService(db_client)
    first = await _create(service, agent_id="agent_1", user_id="user_1",
                          title="Daily Weather Report DS")
    assert first["success"], first

    again = await _create(service, agent_id="agent_1", user_id="user_1",
                          title="Daily Weather Report DS", confirm_new=True)
    assert again["success"] is True
    assert again["is_existing"] is True
    assert again["job_id"] == first["job_id"]
