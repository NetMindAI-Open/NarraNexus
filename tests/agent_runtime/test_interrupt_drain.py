"""
@file_name: test_interrupt_drain.py
@author: Bin Liang
@date: 2026-07-30
@description: _stream_step3_with_interrupt_drain — bounded tail drain.

Interrupt continuity depends on the driver's post-cancel tail (pairing
synthetics, turn_done, PathExecutionResult) reaching ctx; the old
break-on-cancel discarded it. The drain must (a) not change anything for
uncancelled runs, (b) deliver the tail of a well-behaved driver after
cancel, (c) give up within the budget on a stuck driver — Stop must
always complete.
"""
from __future__ import annotations

import asyncio

import pytest

from xyz_agent_context.agent_runtime.agent_runtime import (
    _stream_step3_with_interrupt_drain,
)
from xyz_agent_context.agent_runtime.cancellation import CancellationToken


async def _collect(agen) -> list:
    return [m async for m in agen]


@pytest.mark.asyncio
async def test_uncancelled_run_passes_everything_through():
    async def driver():
        for i in range(5):
            yield f"m{i}"

    token = CancellationToken()
    out = await _collect(_stream_step3_with_interrupt_drain(driver(), token))
    assert out == ["m0", "m1", "m2", "m3", "m4"]


@pytest.mark.asyncio
async def test_cancel_mid_stream_still_delivers_the_tail():
    token = CancellationToken()

    async def driver():
        yield "delta"
        token.cancel("User clicked stop")
        # The driver reacts to cancellation by winding down and yielding
        # its closing events — exactly what must NOT be discarded.
        yield "synthetic_result"
        yield "turn_done"

    out = await _collect(_stream_step3_with_interrupt_drain(driver(), token))
    assert out == ["delta", "synthetic_result", "turn_done"]


@pytest.mark.asyncio
async def test_stuck_driver_is_abandoned_within_budget():
    token = CancellationToken()
    closed = asyncio.Event()

    async def driver():
        try:
            yield "delta"
            token.cancel("User clicked stop")
            await asyncio.sleep(3600)  # driver ignores cancellation
            yield "never"
        finally:
            closed.set()

    out = await _collect(
        _stream_step3_with_interrupt_drain(driver(), token, budget_s=0.1)
    )
    assert out == ["delta"]
    # The generator was explicitly closed, not leaked.
    await asyncio.wait_for(closed.wait(), timeout=1)


@pytest.mark.asyncio
async def test_cancel_before_first_message_still_drains():
    token = CancellationToken()
    token.cancel("stopped instantly")

    async def driver():
        yield "tail_event"

    out = await _collect(_stream_step3_with_interrupt_drain(driver(), token))
    assert out == ["tail_event"]
