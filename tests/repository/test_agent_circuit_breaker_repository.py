"""
@file_name: test_agent_circuit_breaker_repository.py
@author:
@date: 2026-07-13
@description: Repository tests for the Agent circuit-breaker (real sqlite).
"""

import pytest

from xyz_agent_context.repository.agent_circuit_breaker_repository import (
    AgentCircuitBreakerRepository,
)
from xyz_agent_context.schema import CbStatus, ErrorCategory, PausedReason
from xyz_agent_context.utils.timezone import utc_now


@pytest.mark.asyncio
async def test_get_missing_returns_none(db_client):
    repo = AgentCircuitBreakerRepository(db_client)
    assert await repo.get("nope") is None


@pytest.mark.asyncio
async def test_upsert_inserts_then_updates(db_client):
    repo = AgentCircuitBreakerRepository(db_client)

    await repo.upsert_state("agent_a", {
        "cb_status": CbStatus.COOLING.value,
        "consecutive_failure_count": 1,
        "failure_category": ErrorCategory.TRANSIENT.value,
        "cooldown_until": utc_now(),
        "last_error": "boom",
    })
    row = await repo.get("agent_a")
    assert row is not None
    assert row.cb_status == "cooling"
    assert row.consecutive_failure_count == 1
    assert row.failure_category == "transient"

    # Second upsert updates the same row (no duplicate).
    await repo.upsert_state("agent_a", {
        "cb_status": CbStatus.PAUSED.value,
        "consecutive_failure_count": 3,
        "failure_category": ErrorCategory.AUTH.value,
        "paused_reason": PausedReason.AUTH.value,
        "paused_at": utc_now(),
    })
    row = await repo.get("agent_a")
    assert row.cb_status == "paused"
    assert row.consecutive_failure_count == 3
    assert row.paused_reason == "auth"


@pytest.mark.asyncio
async def test_find_paused(db_client):
    repo = AgentCircuitBreakerRepository(db_client)
    await repo.upsert_state("a1", {"cb_status": CbStatus.PAUSED.value,
                                   "paused_reason": PausedReason.AUTH.value})
    await repo.upsert_state("a2", {"cb_status": CbStatus.COOLING.value})
    await repo.upsert_state("a3", {"cb_status": CbStatus.PAUSED.value,
                                   "paused_reason": PausedReason.QUOTA.value})

    paused_ids = {cb.agent_id for cb in await repo.find_paused()}
    assert paused_ids == {"a1", "a3"}
