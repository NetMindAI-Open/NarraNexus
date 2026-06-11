"""
@file_name: test_consolidation_worker.py
@author: Bin Liang
@date: 2026-06-03
@description: Tests for MemoryConsolidationWorker queue state machine.

These tests cover the triggering logic and queue state transitions WITHOUT
calling the LLM — engine.consolidate is monkeypatched to return a fixed count.
This validates that the worker's state machine is correct independently of the
LLM backend.

Scenarios verified:
1. count-based trigger (pending_count >= threshold)
2. idle-based trigger (now - last_dirty_at >= 90s)
3. cap-based trigger (pending_count >= 20)
4. successful consolidation → status resets to 'dirty', pending_count=0,
   last_consolidated_at updated
5. failed consolidation → status='failed', consolidation_failed_at set,
   other scopes not blocked
6. flush_scope() forces processing regardless of thresholds
7. 'processing' rows are skipped by the poll (re-entrancy guard)
"""
from __future__ import annotations

import asyncio
import pytest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from xyz_agent_context.memory.spec import get_spec
from xyz_agent_context.utils.timezone import utc_now


_QUEUE = "memory_consolidation_queue"


# ── fixtures ──────────────────────────────────────────────────────────────────

def _row(
    agent_id="agt1",
    scope_type="agent",
    scope_id="",
    kind="observation",
    pending_count=5,
    last_dirty_at=None,
    last_consolidated_at=None,
    status="dirty",
):
    """Build a minimal queue row dict."""
    now = utc_now()
    return {
        "agent_id": agent_id,
        "scope_type": scope_type,
        "scope_id": scope_id,
        "kind": kind,
        "pending_count": pending_count,
        "last_dirty_at": (last_dirty_at or now).isoformat(),
        "last_consolidated_at": last_consolidated_at.isoformat() if last_consolidated_at else None,
        "status": status,
        "consolidation_failed_at": None,
    }


# ── helper: build worker with mocked engine ──────────────────────────────────

def _make_worker(db_client, consolidate_return=2):
    """Build a MemoryConsolidationWorker; patch MemoryEngine.consolidate."""
    from xyz_agent_context.services.memory_consolidation_worker import (
        MemoryConsolidationWorker,
    )
    worker = MemoryConsolidationWorker(db_client=db_client)
    # Patch at the engine class level so every engine instance the worker
    # creates uses the mock.
    worker._engine_consolidate = AsyncMock(return_value=consolidate_return)
    return worker


# ── tests ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_count_threshold_triggers(db_client):
    """pending_count >= spec.consolidate_threshold → triggered."""
    spec = get_spec("observation")
    row = _row(kind="observation", pending_count=spec.consolidate_threshold)
    await db_client.insert(_QUEUE, row)

    from xyz_agent_context.services.memory_consolidation_worker import (
        MemoryConsolidationWorker,
    )
    worker = MemoryConsolidationWorker(db_client=db_client)
    worker._engine_consolidate = AsyncMock(return_value=1)

    triggered = await worker._collect_triggered_scopes()
    assert len(triggered) == 1, "count threshold must trigger"


@pytest.mark.asyncio
async def test_idle_threshold_triggers(db_client):
    """last_dirty_at >= 90s ago → triggered even with pending_count=1."""
    now = utc_now()
    stale = now - timedelta(seconds=91)
    row = _row(kind="observation", pending_count=1, last_dirty_at=stale)
    await db_client.insert(_QUEUE, row)

    from xyz_agent_context.services.memory_consolidation_worker import (
        MemoryConsolidationWorker,
    )
    worker = MemoryConsolidationWorker(db_client=db_client)
    worker._engine_consolidate = AsyncMock(return_value=1)

    triggered = await worker._collect_triggered_scopes()
    assert len(triggered) == 1, "idle threshold must trigger"


@pytest.mark.asyncio
async def test_fresh_low_count_not_triggered(db_client):
    """Fresh dirty row with pending_count < threshold → not triggered."""
    spec = get_spec("observation")
    row = _row(kind="observation", pending_count=spec.consolidate_threshold - 1)
    await db_client.insert(_QUEUE, row)

    from xyz_agent_context.services.memory_consolidation_worker import (
        MemoryConsolidationWorker,
    )
    worker = MemoryConsolidationWorker(db_client=db_client)
    worker._engine_consolidate = AsyncMock(return_value=1)

    triggered = await worker._collect_triggered_scopes()
    assert len(triggered) == 0, "below threshold must NOT trigger"


@pytest.mark.asyncio
async def test_cap_triggers(db_client):
    """pending_count >= 20 always triggers regardless of spec threshold."""
    row = _row(kind="observation", pending_count=20)
    await db_client.insert(_QUEUE, row)

    from xyz_agent_context.services.memory_consolidation_worker import (
        MemoryConsolidationWorker,
    )
    worker = MemoryConsolidationWorker(db_client=db_client)
    worker._engine_consolidate = AsyncMock(return_value=1)

    triggered = await worker._collect_triggered_scopes()
    assert len(triggered) == 1, "cap=20 must always trigger"


@pytest.mark.asyncio
async def test_successful_consolidation_resets_state(db_client):
    """After a successful consolidation: pending_count=0, status='dirty',
    last_consolidated_at updated."""
    spec = get_spec("observation")
    row = _row(kind="observation", pending_count=spec.consolidate_threshold)
    await db_client.insert(_QUEUE, row)

    from xyz_agent_context.services.memory_consolidation_worker import (
        MemoryConsolidationWorker,
    )
    worker = MemoryConsolidationWorker(db_client=db_client)
    worker._engine_consolidate = AsyncMock(return_value=3)

    await worker._run_one_pass()

    final = await db_client.get_one(_QUEUE, {
        "agent_id": "agt1", "scope_type": "agent", "scope_id": "", "kind": "observation"
    })
    assert final["pending_count"] == 0
    assert final["status"] == "dirty"
    assert final["last_consolidated_at"] is not None
    assert final["consolidation_failed_at"] is None


@pytest.mark.asyncio
async def test_failed_consolidation_isolates_scope(db_client):
    """A scope where consolidation raises → status='failed', does not block
    other scopes from being processed in the same pass."""
    spec = get_spec("observation")

    # Two scopes: one will fail, one will succeed
    await db_client.insert(_QUEUE, _row(
        agent_id="agt_fail", kind="observation", pending_count=spec.consolidate_threshold
    ))
    await db_client.insert(_QUEUE, _row(
        agent_id="agt_ok", kind="observation", pending_count=spec.consolidate_threshold
    ))

    call_count = 0

    async def _selective_consolidate(*args, agent_id, **kwargs):
        nonlocal call_count
        call_count += 1
        if agent_id == "agt_fail":
            raise RuntimeError("simulated LLM failure")
        return 2

    from xyz_agent_context.services.memory_consolidation_worker import (
        MemoryConsolidationWorker,
    )
    worker = MemoryConsolidationWorker(db_client=db_client)
    worker._engine_consolidate = _selective_consolidate

    await worker._run_one_pass()

    fail_row = await db_client.get_one(_QUEUE, {"agent_id": "agt_fail"})
    ok_row = await db_client.get_one(_QUEUE, {"agent_id": "agt_ok"})

    assert fail_row["status"] == "failed"
    assert fail_row["consolidation_failed_at"] is not None
    assert ok_row["status"] == "dirty"
    assert ok_row["pending_count"] == 0


@pytest.mark.asyncio
async def test_processing_rows_skipped(db_client):
    """Rows already in 'processing' status are NOT re-enqueued (re-entrancy guard)."""
    spec = get_spec("observation")
    row = _row(kind="observation", pending_count=spec.consolidate_threshold, status="processing")
    await db_client.insert(_QUEUE, row)

    from xyz_agent_context.services.memory_consolidation_worker import (
        MemoryConsolidationWorker,
    )
    worker = MemoryConsolidationWorker(db_client=db_client)
    worker._engine_consolidate = AsyncMock(return_value=1)

    triggered = await worker._collect_triggered_scopes()
    assert len(triggered) == 0, "'processing' rows must be skipped"


@pytest.mark.asyncio
async def test_flush_scope_forces_processing(db_client):
    """flush_scope() triggers consolidation regardless of count/idle state."""
    # Low count, fresh — would normally not trigger
    row = _row(kind="observation", pending_count=1)
    await db_client.insert(_QUEUE, row)

    from xyz_agent_context.services.memory_consolidation_worker import (
        MemoryConsolidationWorker,
    )
    worker = MemoryConsolidationWorker(db_client=db_client)
    worker._engine_consolidate = AsyncMock(return_value=1)

    await worker.flush_scope(
        agent_id="agt1", scope_type="agent", scope_id="", kind="observation"
    )

    final = await db_client.get_one(_QUEUE, {
        "agent_id": "agt1", "scope_type": "agent", "scope_id": "", "kind": "observation"
    })
    assert final["pending_count"] == 0
    assert final["status"] == "dirty"
    assert final["last_consolidated_at"] is not None


@pytest.mark.asyncio
async def test_failed_rows_not_reprocessed_automatically(db_client):
    """Rows with status='failed' are NOT picked up by the auto-polling pass
    (they are isolated until manually re-queued or recovered)."""
    row = _row(kind="observation", pending_count=10, status="failed")
    await db_client.insert(_QUEUE, row)

    from xyz_agent_context.services.memory_consolidation_worker import (
        MemoryConsolidationWorker,
    )
    worker = MemoryConsolidationWorker(db_client=db_client)
    worker._engine_consolidate = AsyncMock(return_value=1)

    triggered = await worker._collect_triggered_scopes()
    assert len(triggered) == 0, "'failed' rows must not be auto-reprocessed"


# ── 2026-06-11 P0 regression: owner credential injection ────────────────────
#
# The worker runs in the backend lifespan, outside any HTTP request, so the
# auth_middleware ContextVar injection never reaches it. Production now
# resolves the agent OWNER's provider config per scope before calling the
# LLM. These tests pin the wiring.

@pytest.mark.asyncio
async def test_inject_owner_credentials_resolves_for_agent_owner(db_client):
    worker = _make_worker(db_client)
    db_client.get_one = AsyncMock(return_value={"agent_id": "agent_x", "created_by": "user_owner"})

    with patch(
        "xyz_agent_context.services.memory_consolidation_worker.resolve_and_set_provider_for_user",
        new=AsyncMock(),
    ) as resolver:
        await worker._inject_owner_credentials("agent_x")
        resolver.assert_awaited_once_with("user_owner", db_client)


@pytest.mark.asyncio
async def test_inject_owner_credentials_no_owner_is_noop(db_client):
    worker = _make_worker(db_client)
    db_client.get_one = AsyncMock(return_value=None)

    with patch(
        "xyz_agent_context.services.memory_consolidation_worker.resolve_and_set_provider_for_user",
        new=AsyncMock(),
    ) as resolver:
        await worker._inject_owner_credentials("agent_ghost")
        resolver.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolver_failure_isolates_scope_with_facts_intact(db_client):
    """A quota/no-provider verdict raises out of the engine path; the scope
    must be isolated as failed — and because the failure happens BEFORE
    engine.consolidate, no raw fact is tombstoned. pending_count is
    untouched: the facts are still owed a consolidation."""
    worker = _make_worker(db_client)
    worker._engine_consolidate = AsyncMock(side_effect=RuntimeError("FREE_TIER_EXHAUSTED"))

    key = {"agent_id": "agent_q", "scope_type": "agent", "scope_id": "", "kind": "observation"}
    await db_client.insert(
        "memory_consolidation_queue",
        {**key, "status": "dirty", "pending_count": 10,
         "last_dirty_at": utc_now(), "updated_at": utc_now()},
    )

    await worker._process_scope(key)

    row = await db_client.get_one("memory_consolidation_queue", key)
    assert row["status"] == "failed"
    assert row["consolidation_failed_at"] is not None
    assert int(row["pending_count"]) == 10


@pytest.mark.asyncio
async def test_inject_owner_credentials_never_leaks_previous_tenant(db_client):
    """Scope N sets tenant A's config; scope N+1's agent has no owner row.
    The fallback must be the GLOBAL config — not tenant A's leftovers."""
    from xyz_agent_context.agent_framework.api_config import (
        ClaudeConfig, OpenAIConfig, openai_config, set_user_config,
    )

    worker = _make_worker(db_client)
    set_user_config(
        ClaudeConfig(api_key="tenant_a_key"),
        OpenAIConfig(api_key="tenant_a_key"),
    )
    assert openai_config.api_key == "tenant_a_key"

    db_client.get_one = AsyncMock(return_value=None)  # deleted agent
    with patch(
        "xyz_agent_context.services.memory_consolidation_worker.resolve_and_set_provider_for_user",
        new=AsyncMock(),
    ):
        await worker._inject_owner_credentials("agent_deleted")

    assert openai_config.api_key != "tenant_a_key"
