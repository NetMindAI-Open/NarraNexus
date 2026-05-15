"""
@file_name: test_broadcaster.py
@author: Bin Liang
@date: 2026-05-13
@description: Unit tests for Broadcaster — Phase C in-memory pub/sub.
"""
from __future__ import annotations

import asyncio

import pytest

from xyz_agent_context.agent_runtime.broadcaster import Broadcaster


@pytest.mark.asyncio
async def test_subscriber_receives_published_events():
    b = Broadcaster("run_abc")
    sub = b.subscribe("ws1")

    async def consumer():
        events = []
        async for e in sub:
            events.append(e)
        return events

    consumer_task = asyncio.create_task(consumer())
    # Yield once so consumer is parked on the queue
    await asyncio.sleep(0.01)

    b.publish({"type": "foo"})
    b.publish({"type": "bar"})
    # Let async pushes complete
    await asyncio.sleep(0.02)

    b.close()
    events = await consumer_task
    assert [e["type"] for e in events] == ["foo", "bar"]


@pytest.mark.asyncio
async def test_multiple_subscribers_each_get_full_stream():
    b = Broadcaster("run_xyz")
    sub_a = b.subscribe("ws-a")
    sub_b = b.subscribe("ws-b")

    received_a: list[dict] = []
    received_b: list[dict] = []

    async def consume(sub, into):
        async for e in sub:
            into.append(e)

    task_a = asyncio.create_task(consume(sub_a, received_a))
    task_b = asyncio.create_task(consume(sub_b, received_b))
    await asyncio.sleep(0.01)

    for i in range(5):
        b.publish({"type": "event", "i": i})
    await asyncio.sleep(0.05)

    b.close()
    await asyncio.gather(task_a, task_b)

    assert [e["i"] for e in received_a] == [0, 1, 2, 3, 4]
    assert [e["i"] for e in received_b] == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_unsubscribe_does_not_affect_other_subscribers():
    b = Broadcaster("run_xyz")
    sub_a = b.subscribe("ws-a")
    sub_b = b.subscribe("ws-b")

    received_b: list[dict] = []

    async def consume(sub, into):
        async for e in sub:
            into.append(e)

    task_b = asyncio.create_task(consume(sub_b, received_b))
    await asyncio.sleep(0.01)

    # Unsubscribe A — events should still reach B
    b.unsubscribe("ws-a")
    b.publish({"type": "after_a_left"})
    await asyncio.sleep(0.02)

    b.close()
    await task_b
    # also drain A which should have been closed
    drained_a: list[dict] = []
    async for e in sub_a:
        drained_a.append(e)

    assert any(e.get("type") == "after_a_left" for e in received_b)
    # A may have received nothing or only the close sentinel — we just
    # care that it doesn't show up after unsubscribe.
    assert not any(e.get("type") == "after_a_left" for e in drained_a)


@pytest.mark.asyncio
async def test_new_subscriber_receives_current_thinking_buffer():
    """Phase C edge case: a subscriber joining mid-thinking-segment
    gets the buffer snapshot before the live stream."""
    b = Broadcaster("run_seg")
    b.set_current_thinking_buffer("hello world so far")

    sub = b.subscribe("ws-late")
    received: list[dict] = []

    async def consume():
        async for e in sub:
            received.append(e)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.02)

    b.publish({"type": "live_event"})
    await asyncio.sleep(0.02)

    b.close()
    await task

    # First event should be the partial replay
    assert received[0]["type"] == "thinking_partial_replay"
    assert received[0]["content"] == "hello world so far"
    # Then the live event
    assert any(e.get("type") == "live_event" for e in received)


@pytest.mark.asyncio
async def test_close_releases_all_subscribers():
    b = Broadcaster("run_close")
    sub_a = b.subscribe("ws-a")
    sub_b = b.subscribe("ws-b")

    async def consume(sub):
        async for _ in sub:
            pass

    task_a = asyncio.create_task(consume(sub_a))
    task_b = asyncio.create_task(consume(sub_b))
    await asyncio.sleep(0.01)

    b.close()
    # Both consumers should exit promptly
    await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=1.0)
    assert b.is_closed
    assert b.subscriber_count == 0


@pytest.mark.asyncio
async def test_subscribe_after_close_returns_immediately_exhausted():
    b = Broadcaster("run_done")
    b.close()
    sub = b.subscribe("ws-late")

    received = []
    async for e in sub:
        received.append(e)
    assert received == []
