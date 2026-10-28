"""
@file_name: steps.py
@author: Bin Liang
@date: 2026-09-07
@description: The PUBLIC step surface of the agent runtime — what a turn-stage strategy (plugins/builtin.turn or a third-party stage plugin) may call. This is a contract: renaming anything here breaks stage plugins.

The seven-stage TurnPipeline runs strategies; the default strategies are the
former inline step chain, so they need the step bodies. Those bodies live in
the private ``_agent_runtime_steps`` package; this module is the one
non-underscore door to them (plus the two runtime helpers the Act and Commit
stages use). A stage strategy imports from here, never from
``_agent_runtime_steps`` or ``agent_runtime`` internals.
"""
from __future__ import annotations

import asyncio

from loguru import logger

from narranexus.platform.agent_runtime._agent_runtime_steps import (
    RunContext,
    step_0_initialize,
    step_1_5_init_markdown,
    step_1_fast_select,
    step_1_select_narrative,
    step_2_5_sync_instances,
    step_2_load_modules,
    step_3_execute_path,
    step_4_persist_results,
    step_5_execute_hooks,
)
from narranexus.platform.agent_runtime._agent_runtime_steps.step_3_agent_loop import step_3_assemble_context
from narranexus.platform.agent_runtime._agent_runtime_steps.step_5_execute_hooks import build_after_execution_params

#: Seconds the runtime keeps consuming the driver stream AFTER the user
#: cancelled, waiting for the driver's self-termination tail (synthetic
#: tool results, turn_done, PathExecutionResult). Both drivers wind down
#: in well under this on the happy path; the bound exists so a driver
#: that ignores cancellation cannot turn Stop into a hang.
INTERRUPT_DRAIN_BUDGET_S = 8.0


def turn_timing_line(
    *,
    agent_id: str,
    event_id: str,
    source: str,
    pre_s: float,
    setup_s: float,
    loop_s: float,
    persist_s: float,
    total_s: float,
    interrupted: bool,
    profile: str = "",
) -> str:
    """The [turn-timing] log line — a grep-stable contract, so it lives in a
    pure function a test can pin (see test_turn_timing_line.py). Phases:
    pre (run() entry -> Step 0: lazy DB client, agent lookup, service
    construction), setup (Steps 0-2.5), loop (Step 3), persist (Steps
    4+4.6). total covers run() entry to the sync tail's end; the
    backgrounded Steps 5-6 are never counted."""
    line = (
        "[turn-timing] agent={} event={} source={} "
        "pre_s={:.2f} setup_s={:.2f} loop_s={:.2f} persist_s={:.2f} "
        "total_s={:.2f} interrupted={}".format(
            agent_id, event_id, source,
            pre_s, setup_s, loop_s, persist_s, total_s, interrupted,
        )
    )
    if profile:
        # Fast-mode turns append a trailing marker; the base shape stays
        # byte-identical so existing grep one-liners keep matching.
        line += f" profile={profile}"
    return line


async def stream_with_interrupt_drain(
    agen,
    cancellation,
    budget_s: float = INTERRUPT_DRAIN_BUDGET_S,
):
    """Consume the Step-3 stream; on cancellation, drain the tail BOUNDED.

    Interrupt continuity: the driver reacts to cancellation by closing
    the turn properly (pairing synthetics, turn_done) and yielding the
    final ``PathExecutionResult`` — which ``step_3_execute_path`` stores
    on ctx for Steps 4/4.6 to persist. Breaking out of the stream the
    instant cancellation flips (the old behavior) discarded exactly that
    tail, which is why interrupted turns never reached history.

    On budget exhaustion the generator is closed and the caller proceeds
    with whatever was captured (possibly nothing) — Stop always completes.

    Structure notes: exactly ONE ``__anext__`` task is in flight at a
    time (a second concurrent anext on an async generator is a
    RuntimeError), and the uncancelled path RACES that task against
    ``cancellation.await_cancelled()`` — a cancel that lands while we
    are already awaiting the next message must still start the bounded
    clock, or a driver stuck inside that await turns Stop into a hang.
    """
    import time as _time
    from contextlib import suppress as _suppress

    deadline: float | None = None
    pending: "asyncio.Task | None" = None
    while True:
        task: asyncio.Task = (
            pending if pending is not None else asyncio.ensure_future(agen.__anext__())
        )
        pending = task
        if deadline is None and cancellation.is_cancelled:
            logger.info(
                "Cancellation detected during Step 3; draining driver tail "
                f"(bounded, {budget_s:.0f}s)"
            )
            deadline = _time.monotonic() + budget_s
        if deadline is None:
            waiter = asyncio.ensure_future(cancellation.await_cancelled())
            try:
                await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                waiter.cancel()
                with _suppress(asyncio.CancelledError):
                    await waiter
            if not task.done():
                continue  # cancellation fired first; next pass starts the clock
        elif not task.done():
            remaining = deadline - _time.monotonic()
            if remaining > 0:
                await asyncio.wait({task}, timeout=remaining)
            if not task.done():
                logger.warning(
                    "Interrupt drain budget exhausted; abandoning the driver "
                    "tail (the partial turn persists without its closing events)"
                )
                task.cancel()
                with _suppress(BaseException):
                    await task
                with _suppress(Exception):
                    await agen.aclose()
                return
        try:
            msg = task.result()
        except StopAsyncIteration:
            return
        pending = None
        yield msg




__all__ = [
    "INTERRUPT_DRAIN_BUDGET_S",
    "RunContext",
    "build_after_execution_params",
    "step_0_initialize",
    "step_1_5_init_markdown",
    "step_1_fast_select",
    "step_1_select_narrative",
    "step_2_5_sync_instances",
    "step_2_load_modules",
    "step_3_assemble_context",
    "step_3_execute_path",
    "step_4_persist_results",
    "step_5_execute_hooks",
    "stream_with_interrupt_drain",
    "turn_timing_line",
]
