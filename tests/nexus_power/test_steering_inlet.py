"""
@file_name: test_steering_inlet.py
@author: Bin Liang
@date: 2026-08-21
@description: QueueSteeringInlet — the concrete step-boundary steering
inlet (the P4 TriggerInbox seam). Backs the SteeringInlet contract with
an in-process asyncio.Queue that the transport layer feeds; the loop
drains it at each step boundary. Unit behaviour only here; the
end-to-end "a queued message reaches the next model request" proof lives
in test_loop_e2e.py where the real loop harness already exists.
"""

import asyncio
import inspect

import pytest

from narranexus_plugins.frameworks_nexus_power.core.contracts.protocols import (
    SteeringInlet,
)
from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.harness.steering import (
    QueueSteeringInlet,
)


@pytest.mark.asyncio
async def test_drain_returns_queued_messages_in_fifo_order_then_empties():
    queue: asyncio.Queue = asyncio.Queue()
    inlet = QueueSteeringInlet(queue)
    await queue.put({"role": "user", "content": "first"})
    await queue.put({"role": "user", "content": "second"})

    drained = await inlet.drain()

    assert [m["content"] for m in drained] == ["first", "second"]
    # Draining consumes: a second drain with nothing added is empty.
    assert await inlet.drain() == []


@pytest.mark.asyncio
async def test_drain_strips_the_private_steer_id_and_tracks_it_for_consumption():
    # The producer stamps `_steer_id` on a steered message so the loop can report
    # back WHICH steer_inbox rows the run actually consumed. drain() must strip it
    # (the model never sees the platform's bookkeeping key) AND record it, so the
    # loop can emit a steer_consumed signal. Delete the strip and the id leaks to
    # the model request; delete the tracking and consumption can never be acked.
    queue: asyncio.Queue = asyncio.Queue()
    inlet = QueueSteeringInlet(queue)
    await queue.put({"role": "user", "content": "hi", "_steer_id": "m1"})
    await queue.put({"role": "user", "content": "ho", "_steer_id": "m2"})

    drained = await inlet.drain()

    assert [m["content"] for m in drained] == ["hi", "ho"]
    assert all("_steer_id" not in m for m in drained)  # stripped, not sent to model
    assert inlet.take_consumed() == ["m1", "m2"]  # tracked, in order
    assert inlet.take_consumed() == []  # take clears — reported once


@pytest.mark.asyncio
async def test_drain_of_messages_without_a_steer_id_tracks_nothing():
    queue: asyncio.Queue = asyncio.Queue()
    inlet = QueueSteeringInlet(queue)
    await queue.put({"role": "user", "content": "plain"})  # no _steer_id
    await inlet.drain()
    assert inlet.take_consumed() == []


@pytest.mark.asyncio
async def test_drain_on_empty_queue_returns_empty_list_without_blocking():
    inlet = QueueSteeringInlet(asyncio.Queue())
    # Must be non-blocking: the loop calls drain() every step boundary and
    # an empty inlet is the common case — it may never await a put().
    drained = await asyncio.wait_for(inlet.drain(), timeout=0.5)
    assert drained == []


@pytest.mark.asyncio
async def test_messages_put_between_drains_are_seen_by_the_later_drain():
    queue: asyncio.Queue = asyncio.Queue()
    inlet = QueueSteeringInlet(queue)

    assert await inlet.drain() == []
    await queue.put({"role": "user", "content": "arrived mid-turn"})
    drained = await inlet.drain()

    assert [m["content"] for m in drained] == ["arrived mid-turn"]


class _Cancel:
    """Minimal CancellationSignal double: a flag polled via requested()."""

    def __init__(self, flag: bool = False) -> None:
        self._flag = flag

    def trip(self) -> None:
        self._flag = True

    def requested(self) -> bool:
        return self._flag


@pytest.mark.asyncio
async def test_wait_for_input_returns_immediately_when_something_is_already_queued():
    queue: asyncio.Queue = asyncio.Queue()
    inlet = QueueSteeringInlet(queue)
    await queue.put({"role": "user", "content": "already here"})

    # Must not wait the whole timeout when a message is already present.
    got = await asyncio.wait_for(inlet.wait_for_input(10.0, _Cancel()), timeout=0.5)
    assert [m["content"] for m in got] == ["already here"]


@pytest.mark.asyncio
async def test_wait_for_input_blocks_until_a_message_arrives_then_fuses_the_rest():
    queue: asyncio.Queue = asyncio.Queue()
    inlet = QueueSteeringInlet(queue)

    async def _produce():
        await asyncio.sleep(0.05)
        await queue.put({"role": "user", "content": "late-1"})
        await queue.put({"role": "user", "content": "late-2"})

    producer = asyncio.create_task(_produce())
    got = await asyncio.wait_for(inlet.wait_for_input(5.0, _Cancel()), timeout=2.0)
    await producer

    # Woken by the first arrival AND fuses everything queued by then (one wait,
    # possibly several messages — the "fusion is immediate" contract).
    assert [m["content"] for m in got] == ["late-1", "late-2"]


@pytest.mark.asyncio
async def test_wait_for_input_strips_and_tracks_steer_id_including_the_blocked_first():
    # Integration with the consumption contract: a message steered into a WAITING
    # run must strip _steer_id (model never sees it) AND be tracked as consumed,
    # including the FIRST item that arrives while blocked (it bypasses drain, so
    # wait_for_input must _take_one it too), so the producer's cursor advances.
    queue: asyncio.Queue = asyncio.Queue()
    inlet = QueueSteeringInlet(queue)

    async def _late():
        await asyncio.sleep(0.05)
        await queue.put({"role": "user", "content": "hi", "_steer_id": "m1"})
        await queue.put({"role": "user", "content": "ho", "_steer_id": "m2"})

    producer = asyncio.create_task(_late())
    got = await asyncio.wait_for(inlet.wait_for_input(5.0, _Cancel()), timeout=2.0)
    await producer

    assert [m["content"] for m in got] == ["hi", "ho"]
    assert all("_steer_id" not in m for m in got)  # stripped from every item
    assert inlet.take_consumed() == ["m1", "m2"]  # blocked-first + fused rest


@pytest.mark.asyncio
async def test_wait_for_input_returns_empty_on_timeout():
    inlet = QueueSteeringInlet(asyncio.Queue())
    got = await asyncio.wait_for(inlet.wait_for_input(0.1, _Cancel()), timeout=1.0)
    assert got == []  # timeout: nothing arrived


@pytest.mark.asyncio
async def test_wait_for_input_returns_promptly_when_cancelled_not_after_full_timeout():
    inlet = QueueSteeringInlet(asyncio.Queue())
    cancel = _Cancel()

    async def _trip():
        await asyncio.sleep(0.05)
        cancel.trip()

    tripper = asyncio.create_task(_trip())
    # timeout is long; a tripped cancel must return well before it elapses.
    got = await asyncio.wait_for(inlet.wait_for_input(30.0, cancel), timeout=1.0)
    await tripper
    assert got == []  # interrupted; the loop then re-checks cancel and interrupts


@pytest.mark.asyncio
async def test_wait_for_input_does_not_lose_a_message_that_arrives_at_the_deadline():
    # The classic wait_for(queue.get(), t) hazard: a get cancelled by timeout can
    # drop an item that was already dequeued. wait_for_input must deliver a
    # message put right at the deadline EXACTLY ONCE — never lost, never doubled.
    queue: asyncio.Queue = asyncio.Queue()
    inlet = QueueSteeringInlet(queue)

    async def _late():
        await asyncio.sleep(0.09)
        await queue.put({"role": "user", "content": "edge"})

    late = asyncio.create_task(_late())
    got = await asyncio.wait_for(inlet.wait_for_input(0.1, _Cancel()), timeout=1.0)
    await late

    # Present in EXACTLY ONE of the two surfaces: either wait_for_input returned it
    # (the deadline branch's final drain now sweeps it up straight away — the
    # usual outcome) OR it is still queued for the next DRAIN. Summing both and
    # asserting a single "edge" pins "delivered once": a loss makes this empty, a
    # double (dequeued by the cancelled get AND re-queued) makes it two.
    leftover = await inlet.drain()
    delivered = [m["content"] for m in got] + [m["content"] for m in leftover]
    assert delivered == ["edge"]


@pytest.mark.asyncio
async def test_satisfies_the_steering_inlet_protocol():
    # Guards against the concrete inlet drifting away from the protocol
    # the loop depends on. isinstance against the @runtime_checkable
    # Protocol catches a missing method if SteeringInlet grows one; the
    # coroutine + list checks pin the shape isinstance cannot see (it
    # checks presence, not signature).
    inlet = QueueSteeringInlet(asyncio.Queue())
    assert isinstance(inlet, SteeringInlet)
    assert inspect.iscoroutinefunction(inlet.drain)
    assert isinstance(await inlet.drain(), list)
    # The blocking twin is part of the contract the loop depends on.
    assert inspect.iscoroutinefunction(inlet.wait_for_input)
    assert isinstance(await inlet.wait_for_input(0.0, _Cancel()), list)
