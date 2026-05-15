"""
@file_name: test_cancellation.py
@author: Bin Liang
@date: 2026-05-13
@description: Unit tests for CancellationToken — focused on the
              ``await_cancelled()`` race method added in Phase A.

The pre-existing ``is_cancelled`` / ``raise_if_cancelled`` / ``cancel``
methods are covered indirectly by every step that imports the module.
These tests target the new ``await_cancelled`` coroutine which is the
mechanism that lets ``xyz_claude_agent_sdk.agent_loop`` race "next LLM
message" against "user pressed Stop".
"""
from __future__ import annotations

import asyncio
import time

import pytest

from xyz_agent_context.agent_runtime.cancellation import (
    CancellationToken,
    CancelledByUser,
)


@pytest.mark.asyncio
async def test_await_cancelled_returns_when_cancel_fires():
    """The coroutine blocks until cancel() is called from elsewhere."""
    token = CancellationToken()

    async def canceller():
        await asyncio.sleep(0.05)
        token.cancel("test fired")

    start = time.monotonic()
    await asyncio.gather(token.await_cancelled(), canceller())
    elapsed = time.monotonic() - start

    assert token.is_cancelled
    assert token.reason == "test fired"
    # Generous bound — CI hosts can be slow but 1 s is plenty.
    assert elapsed < 1.0


@pytest.mark.asyncio
async def test_await_cancelled_returns_immediately_if_already_cancelled():
    """Already-cancelled tokens resolve the await immediately — same
    semantics as asyncio.Event.wait()."""
    token = CancellationToken()
    token.cancel("pre-cancelled")

    start = time.monotonic()
    await token.await_cancelled()
    elapsed = time.monotonic() - start

    # Should be effectively zero. 50 ms is loose enough for any CI host.
    assert elapsed < 0.05


@pytest.mark.asyncio
async def test_await_cancelled_in_race_with_slow_message():
    """The canonical use case: race against an asyncio.wait_for and let
    cancellation win even when the message would have taken much longer."""
    token = CancellationToken()

    async def slow_message():
        # Stand-in for ``response_iter.__anext__()`` while a Bash tool
        # call is running for tens of seconds.
        await asyncio.sleep(5.0)
        return "message that should never be received"

    msg_task = asyncio.create_task(slow_message())
    cancel_task = asyncio.create_task(token.await_cancelled())

    async def fire_cancel():
        await asyncio.sleep(0.05)
        token.cancel("race winner")

    asyncio.create_task(fire_cancel())

    start = time.monotonic()
    done, pending = await asyncio.wait(
        [msg_task, cancel_task],
        return_when=asyncio.FIRST_COMPLETED,
        timeout=2.0,
    )
    elapsed = time.monotonic() - start

    for task in pending:
        task.cancel()

    assert cancel_task in done
    assert msg_task in pending  # message never arrived — cancelled
    assert elapsed < 0.5  # cancel won, not the 5 s message
    assert token.is_cancelled


@pytest.mark.asyncio
async def test_multiple_awaiters_all_unblock_on_single_cancel():
    """Several coroutines awaiting the same token all unblock when one
    cancel call fires. The contract is asyncio.Event-like."""
    token = CancellationToken()

    waiter_count = 5
    waiters = [asyncio.create_task(token.await_cancelled()) for _ in range(waiter_count)]

    # All blocked
    await asyncio.sleep(0.01)
    assert all(not w.done() for w in waiters)

    # Single cancel
    token.cancel("broadcast")

    # All unblock
    done, pending = await asyncio.wait(waiters, timeout=1.0)
    assert len(done) == waiter_count
    assert not pending


def test_raise_if_cancelled_still_works():
    """Sanity: the existing API surface is unchanged. await_cancelled is
    purely additive."""
    token = CancellationToken()
    token.raise_if_cancelled()  # no-op

    token.cancel("for raise")
    with pytest.raises(CancelledByUser) as excinfo:
        token.raise_if_cancelled()
    assert excinfo.value.reason == "for raise"


def test_cancel_is_idempotent_with_first_reason_winning():
    """Multiple cancel() calls — only the first reason sticks."""
    token = CancellationToken()
    token.cancel("first")
    token.cancel("second")
    token.cancel("third")
    assert token.reason == "first"
    assert token.is_cancelled
