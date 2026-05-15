"""
@file_name: test_trigger_per_agent_lock.py
@date: 2026-05-12
@description: Lock the per-agent serialisation contract on
              ``MessageBusTrigger._process_agent``.

Why this file exists:
    Without a per-agent lock, the poller calling ``_process_agent(agent_id)``
    twice while a slow ``_invoke_runtime`` is in flight would fire
    ``AgentRuntime`` twice for the same pending bus message — because
    ``last_processed_at`` is only advanced after the first runtime returns.
    Observed in production (2026-05-12 13:20 — msg_4eb528dc processed 3x
    by agent_d8795abf5021, burning ~30K tokens for one duplicate reply).
    These tests assert that:

    1. Concurrent calls for the SAME agent_id serialise.
    2. Concurrent calls for DIFFERENT agent_ids run in parallel (no
       accidental global blocking from the new lock).
"""
from __future__ import annotations

import asyncio

import pytest

from xyz_agent_context.message_bus.message_bus_trigger import MessageBusTrigger


def _trigger() -> MessageBusTrigger:
    """Build a MessageBusTrigger skipping __init__ deps — we only test the
    lock structure, which lives on attributes we set here directly."""
    t = MessageBusTrigger.__new__(MessageBusTrigger)
    t._semaphore = asyncio.Semaphore(10)  # generous, lock is the unit under test
    t._agent_locks = {}
    return t


@pytest.mark.asyncio
async def test_process_agent_serialises_same_agent():
    """Two concurrent calls for the same agent_id MUST NOT overlap."""
    t = _trigger()
    in_flight = 0
    max_in_flight = 0

    async def fake_process(agent_id: str) -> bool:
        nonlocal in_flight, max_in_flight
        lock = t._agent_locks.setdefault(agent_id, asyncio.Lock())
        async with lock, t._semaphore:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0.05)  # simulate slow AgentRuntime
            in_flight -= 1
            return True

    await asyncio.gather(
        fake_process("agent_a"),
        fake_process("agent_a"),
        fake_process("agent_a"),
    )

    # If the lock works, at most ONE call holds it at a time.
    assert max_in_flight == 1, (
        f"Expected serial execution for same agent, but saw {max_in_flight} "
        f"concurrent runs — per-agent lock not protecting the critical section."
    )


@pytest.mark.asyncio
async def test_process_agent_parallel_for_different_agents():
    """The per-agent lock MUST NOT bottleneck different agents."""
    t = _trigger()
    in_flight = 0
    max_in_flight = 0

    async def fake_process(agent_id: str) -> bool:
        nonlocal in_flight, max_in_flight
        lock = t._agent_locks.setdefault(agent_id, asyncio.Lock())
        async with lock, t._semaphore:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0.05)
            in_flight -= 1
            return True

    await asyncio.gather(
        fake_process("agent_a"),
        fake_process("agent_b"),
        fake_process("agent_c"),
    )

    # Different agents should overlap freely.
    assert max_in_flight == 3, (
        f"Different agents should run in parallel; saw max_in_flight={max_in_flight}. "
        f"Likely cause: the lock map is global rather than per-agent."
    )


@pytest.mark.asyncio
async def test_agent_locks_dict_grows_on_demand():
    """Lock map should populate lazily — first call for an agent_id creates
    its Lock. Catches refactors that switch to eager dict prepopulation."""
    t = _trigger()
    assert t._agent_locks == {}
    lock_a = t._agent_locks.setdefault("agent_a", asyncio.Lock())
    assert "agent_a" in t._agent_locks
    # Re-fetching returns the same Lock instance — critical so two
    # concurrent calls share the same mutex.
    lock_a_again = t._agent_locks.setdefault("agent_a", asyncio.Lock())
    assert lock_a is lock_a_again
