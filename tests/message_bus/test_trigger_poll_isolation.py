"""
@file_name: test_trigger_poll_isolation.py
@date: 2026-07-28
@description: The poll loop must survive a turn that never returns, and must
say so out loud.

Guards the 2026-07-27 production stall: the loop ``asyncio.gather``ed every
agent and awaited all of them, so one coroutine that never came back — an LLM
connection wedged with no timeout, holding one of three worker slots — froze
message delivery for **every** user for 33 hours. No exception was raised, so
nothing restarted, and the only liveness signal in existence ("the asyncio task
object still exists") kept reporting healthy the entire time.

Two properties are pinned here:

1. **Isolation** — a hung dispatch does not stop the loop from cycling or from
   dispatching other agents.
2. **Observability** — the work counters advance so a wedge is visible in the
   audit trail within a minute instead of never.

Note what is NOT tested, because it must never exist: nothing here force-stops
a slow turn. A multi-hour run is a legitimate workload (binding rule #14); the
failure this guards is our loop dying, not an agent taking its time.
"""

from __future__ import annotations

import asyncio

import pytest

from xyz_agent_context.message_bus.local_bus import LocalMessageBus
from xyz_agent_context.message_bus.message_bus_trigger import MessageBusTrigger

ROOM = "ch_team"


async def _seed_room(db_client, members: list[str]) -> LocalMessageBus:
    await db_client.insert("bus_channels", {
        "channel_id": ROOM, "name": "room", "channel_type": "group",
        "created_by": "team_t1",
    })
    for aid in members:
        await db_client.insert("bus_channel_members", {
            "channel_id": ROOM, "agent_id": aid,
        })
    return LocalMessageBus(db_client._backend)


async def _say(bus, mentions: list[str]) -> None:
    await bus.send_message(
        from_agent="usr_u1", to_channel=ROOM, content="work please", mentions=mentions,
    )


def _trigger(bus, max_workers: int = 3) -> MessageBusTrigger:
    t = MessageBusTrigger(bus=bus, max_workers=max_workers)
    t.audit = _NullAudit()
    return t


class _NullAudit:
    """Swallow audit writes — this file is about the loop, not the sink."""

    def __init__(self):
        self.events: list[tuple[str, object]] = []

    async def started(self, detail=None):
        self.events.append(("started", detail))

    async def stopped(self, detail=None):
        self.events.append(("stopped", detail))

    async def error(self, detail=None):
        self.events.append(("error", detail))

    async def heartbeat(self, detail=None, force=False):
        self.events.append(("heartbeat", detail))


@pytest.mark.asyncio
async def test_a_quiet_room_produces_no_candidates(db_client):
    """The old cycle woke EVERY channel member every few seconds — 364 of them
    on prod — each of which then queried its own pending list, plus a poison
    lookup per row, just to conclude it had nothing to do."""
    bus = await _seed_room(db_client, ["agent_a", "agent_b", "agent_c"])
    t = _trigger(bus)
    assert await t._agents_with_pending() == []


@pytest.mark.asyncio
async def test_every_member_of_a_noisy_room_is_a_candidate(db_client):
    """Deliberate over-inclusion, and it is load-bearing.

    The candidate query only asks "is there a message past your cursor" — it
    does NOT apply the @mention filter. It must not: an un-addressed member is
    exactly who needs to be dispatched so `_process_agent` can ACK the message
    and move its cursor past it. Filter them out here and their cursors freeze,
    they stay candidates forever, and the scan never converges.
    """
    bus = await _seed_room(db_client, ["agent_a", "agent_b", "agent_c"])
    await _say(bus, ["agent_a"])

    assert sorted(await _trigger(bus)._agents_with_pending()) == [
        "agent_a", "agent_b", "agent_c",
    ]


@pytest.mark.asyncio
async def test_candidates_drain_once_cursors_advance(db_client):
    """The corollary of the above: after everyone has been processed, the scan
    goes quiet again instead of re-dispatching the room forever."""
    bus = await _seed_room(db_client, ["agent_a", "agent_b"])
    await _say(bus, ["agent_a"])

    latest = (await bus.get_messages(ROOM, limit=10))[-1]
    for aid in ("agent_a", "agent_b"):
        await bus.ack_processed(aid, ROOM, latest.created_at)

    assert await _trigger(bus)._agents_with_pending() == []


@pytest.mark.asyncio
async def test_a_sender_is_not_a_candidate_for_its_own_message(db_client):
    bus = await _seed_room(db_client, ["agent_a", "agent_b"])
    await bus.send_message(
        from_agent="agent_a", to_channel=ROOM, content="hi", mentions=["agent_b"],
    )
    t = _trigger(bus)
    assert await t._agents_with_pending() == ["agent_b"]


@pytest.mark.asyncio
async def test_a_hung_turn_does_not_freeze_the_cycle(db_client):
    """THE regression. agent_a's turn never returns; the loop must keep going
    and must still dispatch agent_b."""
    bus = await _seed_room(db_client, ["agent_a", "agent_b"])
    await _say(bus, ["agent_a", "agent_b"])

    t = _trigger(bus)
    started: list[str] = []
    release = asyncio.Event()

    async def fake_process(agent_id: str) -> bool:
        started.append(agent_id)
        if agent_id == "agent_a":
            await release.wait()  # never set during the assertions
        return True

    t._process_agent = fake_process

    # First cycle dispatches both; it must RETURN even though agent_a hangs.
    dispatched = await asyncio.wait_for(t._poll_cycle(), timeout=2)
    assert dispatched == 2
    await asyncio.sleep(0)  # let the dispatch tasks reach their first await

    # Further cycles keep completing while agent_a is still stuck.
    for _ in range(3):
        await asyncio.wait_for(t._poll_cycle(), timeout=2)
    assert t._cycles == 4

    assert "agent_a" in t._in_flight, "the stuck turn should still be tracked"
    release.set()
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_an_agent_is_not_dispatched_twice_while_in_flight(db_client):
    bus = await _seed_room(db_client, ["agent_a"])
    await _say(bus, ["agent_a"])

    t = _trigger(bus)
    calls: list[str] = []
    release = asyncio.Event()

    async def fake_process(agent_id: str) -> bool:
        calls.append(agent_id)
        await release.wait()
        return True

    t._process_agent = fake_process

    assert await t._poll_cycle() == 1
    await asyncio.sleep(0)
    assert await t._poll_cycle() == 0  # still in flight → not re-dispatched
    assert calls == ["agent_a"]

    release.set()
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_a_finished_dispatch_leaves_the_registry(db_client):
    bus = await _seed_room(db_client, ["agent_a"])
    await _say(bus, ["agent_a"])

    t = _trigger(bus)

    async def fake_process(agent_id: str) -> bool:
        return True

    t._process_agent = fake_process

    await t._poll_cycle()
    await asyncio.sleep(0.05)
    assert t._in_flight == {}
    assert t._handled_total == 1


@pytest.mark.asyncio
async def test_a_dispatch_that_raises_is_logged_and_released(db_client):
    """A crashing turn must not wedge the agent's slot in `_in_flight` forever
    — that would silently stop the agent from ever being dispatched again."""
    bus = await _seed_room(db_client, ["agent_a"])
    await _say(bus, ["agent_a"])

    t = _trigger(bus)

    async def boom(agent_id: str) -> bool:
        raise RuntimeError("turn exploded")

    t._process_agent = boom

    await t._poll_cycle()
    await asyncio.sleep(0.05)
    assert t._in_flight == {}
    assert await t._poll_cycle() == 1  # dispatchable again


@pytest.mark.asyncio
async def test_liveness_snapshot_separates_running_from_waiting(db_client):
    """Sustained `running == max_workers` with `waiting > 0` is the signal that
    the worker pool, not the agents, is the bottleneck."""
    bus = await _seed_room(db_client, ["agent_a", "agent_b", "agent_c"])
    await _say(bus, ["agent_a", "agent_b", "agent_c"])

    t = _trigger(bus, max_workers=1)
    release = asyncio.Event()

    async def fake_process(agent_id: str) -> bool:
        async with t._semaphore:
            flight = t._in_flight.get(agent_id)
            if flight is not None:
                flight.running = True
            await release.wait()
        return True

    t._process_agent = fake_process

    await t._poll_cycle()
    await asyncio.sleep(0.05)

    snap = t.liveness_snapshot()
    assert snap["running"] == 1
    assert snap["waiting"] == 2
    assert snap["max_workers"] == 1
    assert snap["longest_running_agent"] is not None

    release.set()
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_counters_distinguish_wedged_from_idle(db_client):
    """An idle loop and a wedged loop look identical from outside unless the
    counters say otherwise: `cycles` advances in both, `candidates` does not."""
    bus = await _seed_room(db_client, ["agent_a"])
    t = _trigger(bus)

    await t._poll_cycle()
    idle = t.liveness_snapshot()
    assert idle["cycles"] == 1
    assert idle["candidates"] == 0
    assert idle["dispatched_total"] == 0
    assert idle["last_dispatch_at"] is None

    await _say(bus, ["agent_a"])

    async def fake_process(agent_id: str) -> bool:
        return True

    t._process_agent = fake_process
    await t._poll_cycle()
    await asyncio.sleep(0.05)

    busy = t.liveness_snapshot()
    assert busy["cycles"] == 2
    assert busy["candidates"] == 1
    assert busy["dispatched_total"] == 1
    assert busy["last_dispatch_at"] is not None


@pytest.mark.asyncio
async def test_stop_cancels_in_flight_dispatches(db_client):
    bus = await _seed_room(db_client, ["agent_a"])
    await _say(bus, ["agent_a"])

    t = _trigger(bus)
    release = asyncio.Event()

    async def fake_process(agent_id: str) -> bool:
        await release.wait()
        return True

    t._process_agent = fake_process

    await t._poll_cycle()
    await asyncio.sleep(0)
    task = t._in_flight["agent_a"].task

    t.stop()
    await asyncio.sleep(0.05)
    assert task.cancelled()
    assert t._in_flight == {}


@pytest.mark.asyncio
async def test_the_loop_audits_start_stop_and_progress(db_client):
    bus = await _seed_room(db_client, ["agent_a"])
    t = _trigger(bus)

    loop_task = asyncio.create_task(t.start())
    await asyncio.sleep(0.05)
    t.stop()
    await asyncio.wait_for(loop_task, timeout=5)

    kinds = [k for k, _ in t.audit.events]
    assert kinds[0] == "started"
    assert kinds[-1] == "stopped"
    assert "heartbeat" in kinds
    # The heartbeat carries work counters, not just "I am alive".
    beat = next(d for k, d in t.audit.events if k == "heartbeat")
    assert {"cycles", "candidates", "dispatched_total", "running", "waiting"} <= set(beat)


@pytest.mark.asyncio
async def test_a_failing_cycle_is_audited_and_the_loop_continues(db_client):
    bus = await _seed_room(db_client, ["agent_a"])
    t = _trigger(bus)
    calls = {"n": 0}

    async def flaky_cycle() -> int:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("scan query failed")
        return 0

    t._poll_cycle = flaky_cycle
    t._current_interval = 0

    loop_task = asyncio.create_task(t.start())
    await asyncio.sleep(0.05)
    t.stop()
    await asyncio.wait_for(loop_task, timeout=5)

    assert calls["n"] > 1, "one bad cycle must not end the loop"
    assert any(k == "error" for k, _ in t.audit.events)
