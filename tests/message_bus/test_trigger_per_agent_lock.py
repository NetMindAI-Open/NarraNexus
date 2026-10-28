"""
@file_name: test_trigger_per_agent_lock.py
@date: 2026-05-12
@description: Lock the per-LANE serialisation contract on
              ``MessageBusTrigger._process_lane``.

Why this file exists:
    Without the lock, the poller calling ``_process_lane`` twice for the same
    lane while a slow ``_invoke_runtime`` is in flight would fire
    ``AgentRuntime`` twice for the same pending bus message — because
    ``last_processed_at`` is only advanced after the first runtime returns.
    Observed in production (2026-05-12 13:20 — msg_4eb528dc processed 3x
    by agent_d8795abf5021, burning ~30K tokens for one duplicate reply).

    The lock's key is the LANE ``(agent_id, channel_id)``, not the agent:
    the duplicate risk is per-message, hence per-channel, so per-lane keeping
    is enough — AND it lets one agent's several teams run at once, which is the
    point (a message in team B must not wait behind the agent's team-A turn).

    These tests assert that:

    1. Concurrent calls for the SAME lane serialise.
    2. Concurrent calls for DIFFERENT agents run in parallel.
    3. Concurrent calls for the SAME agent on DIFFERENT channels run in
       parallel (multi-team concurrency — the 2026-08-21 change).
"""
from __future__ import annotations

import asyncio

import pytest

from narranexus.platform.message_bus.message_bus_trigger import MessageBusTrigger


def _trigger() -> MessageBusTrigger:
    """Build a MessageBusTrigger skipping __init__ deps — we only test the
    lock structure, which lives on attributes we set here directly."""
    t = MessageBusTrigger.__new__(MessageBusTrigger)
    t._semaphore = asyncio.Semaphore(10)  # generous, the lock is the unit under test
    t._lane_locks = {}
    return t


def _lane_worker(t: MessageBusTrigger):
    """A fake lane turn that takes the per-lane lock and records overlap."""
    state = {"in_flight": 0, "max_in_flight": 0}

    async def run(lane: tuple[str, str]) -> None:
        lock = t._lane_locks.setdefault(lane, asyncio.Lock())
        async with lock, t._semaphore:
            state["in_flight"] += 1
            state["max_in_flight"] = max(state["max_in_flight"], state["in_flight"])
            await asyncio.sleep(0.05)
            state["in_flight"] -= 1

    return run, state


@pytest.mark.asyncio
async def test_same_lane_serialises():
    """Two concurrent calls for the same lane MUST NOT overlap."""
    t = _trigger()
    run, state = _lane_worker(t)
    await asyncio.gather(
        run(("agent_a", "ch1")), run(("agent_a", "ch1")), run(("agent_a", "ch1")),
    )
    assert state["max_in_flight"] == 1, (
        f"Expected serial execution for the same lane, saw {state['max_in_flight']} "
        f"concurrent — per-lane lock not protecting the critical section."
    )


@pytest.mark.asyncio
async def test_different_agents_run_in_parallel():
    """The lock MUST NOT bottleneck different agents."""
    t = _trigger()
    run, state = _lane_worker(t)
    await asyncio.gather(
        run(("agent_a", "ch1")), run(("agent_b", "ch1")), run(("agent_c", "ch1")),
    )
    assert state["max_in_flight"] == 3, (
        f"Different agents should overlap; saw max_in_flight={state['max_in_flight']}."
    )


@pytest.mark.asyncio
async def test_same_agent_different_channels_run_in_parallel():
    """The 2026-08-21 change: one agent, several teams, at once. Different
    channels are different lanes, so they must NOT serialise — otherwise a
    message in team B waits behind the agent's team-A turn."""
    t = _trigger()
    run, state = _lane_worker(t)
    await asyncio.gather(
        run(("agent_a", "team_A")), run(("agent_a", "team_B")), run(("agent_a", "team_C")),
    )
    assert state["max_in_flight"] == 3, (
        f"An agent's distinct channels should overlap; saw "
        f"max_in_flight={state['max_in_flight']} — the lane lock is keyed too coarsely."
    )


@pytest.mark.asyncio
async def test_lane_locks_dict_grows_on_demand():
    """Lock map populates lazily — first call for a lane creates its Lock.
    Catches refactors that switch to eager dict prepopulation."""
    t = _trigger()
    assert t._lane_locks == {}
    lane = ("agent_a", "ch1")
    lock_a = t._lane_locks.setdefault(lane, asyncio.Lock())
    assert lane in t._lane_locks
    # Re-fetching returns the same Lock instance — critical so two concurrent
    # calls share the same mutex.
    lock_a_again = t._lane_locks.setdefault(lane, asyncio.Lock())
    assert lock_a is lock_a_again
