"""
@file_name: test_agent_loop_cancel_race.py
@author: Bin Liang
@date: 2026-05-13
@description: Verify the race-with-cancel pattern in xyz_claude_agent_sdk
              break the receive loop within ~100 ms even when no message
              arrives from Claude CLI.

The test exercises the wait-pattern logic itself in isolation — we do
not boot a real Claude CLI subprocess. Instead we build a tiny async
iterator that NEVER yields and assert that cancellation drives the
loop out of ``await asyncio.wait(...)`` within bounded latency.

The previous implementation used ``asyncio.wait_for(__anext__(),
timeout=IDLE_TIMEOUT_SECONDS)`` which could only check ``is_cancelled``
AFTER a message arrived; for the slow-or-stuck-tool case (the Xiong
bug) it meant Stop took tens of seconds to register.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from xyz_agent_context.agent_runtime.cancellation import CancellationToken


class _NeverYields:
    """Async iterator stand-in for ``client.receive_response().__aiter__()``
    that never produces a message — simulating a stuck Claude CLI / a
    Bash tool taking forever."""

    def __aiter__(self):
        return self

    async def __anext__(self):
        # Wait essentially forever. The test cancels us before this returns.
        await asyncio.sleep(3600)
        return "should never get here"


@pytest.mark.asyncio
async def test_cancel_breaks_silent_receive_within_100ms():
    """The exact pattern used in xyz_claude_agent_sdk:agent_loop —
    asyncio.wait over (message, cancel). When no message arrives but
    cancel fires, we must break out within ~100 ms (regardless of the
    600 s IDLE_TIMEOUT_SECONDS upper bound)."""
    cancellation = CancellationToken()
    response_iter = _NeverYields().__aiter__()
    IDLE_TIMEOUT_SECONDS = 600.0  # match production

    # Fire cancellation after 50 ms — same shape as a user clicking Stop
    # while a Bash tool is running.
    async def fire_cancel():
        await asyncio.sleep(0.05)
        cancellation.cancel("test stop")

    asyncio.create_task(fire_cancel())

    start = time.monotonic()
    message_task = asyncio.create_task(response_iter.__anext__())
    cancel_task = asyncio.create_task(cancellation.await_cancelled())

    done, pending = await asyncio.wait(
        [message_task, cancel_task],
        return_when=asyncio.FIRST_COMPLETED,
        timeout=IDLE_TIMEOUT_SECONDS,
    )
    # Cleanup pending
    for task in pending:
        task.cancel()

    elapsed = time.monotonic() - start

    assert cancellation.is_cancelled
    assert cancel_task in done
    assert message_task in pending  # message never came — cancel won
    # The race should resolve in well under a second. 500 ms is loose
    # enough for any CI host while still failing the old wait_for(600s)
    # behaviour by a factor of ~1000.
    assert elapsed < 0.5, f"race took {elapsed:.3f}s, expected <0.5s"


@pytest.mark.asyncio
async def test_message_wins_when_it_arrives_before_cancel():
    """The race is symmetric: when a message arrives and cancellation
    has NOT fired, message_task wins and processing continues."""
    cancellation = CancellationToken()

    class _OneMessage:
        def __init__(self):
            self._sent = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._sent:
                raise StopAsyncIteration
            self._sent = True
            await asyncio.sleep(0.02)
            return "the message"

    response_iter = _OneMessage().__aiter__()

    message_task = asyncio.create_task(response_iter.__anext__())
    cancel_task = asyncio.create_task(cancellation.await_cancelled())

    done, pending = await asyncio.wait(
        [message_task, cancel_task],
        return_when=asyncio.FIRST_COMPLETED,
        timeout=1.0,
    )
    for task in pending:
        task.cancel()

    assert not cancellation.is_cancelled
    assert message_task in done
    assert cancel_task in pending
    assert message_task.result() == "the message"
