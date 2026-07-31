"""
@file_name: test_interrupt.py
@author: Bin Liang
@date: 2026-07-30
@description: Mid-stream interruption — the loop must abort the provider
stream at the earliest observable point and close as INTERRUPTED.

Before this, cancellation only landed at phase boundaries: a stop
pressed during a long generation waited for the whole model stream to
finish, and a stream that ended with no tool calls would then close as
NO_MORE_ACTIONS — the turn looked like a natural stop, not a user
interrupt.
"""
from __future__ import annotations

import dataclasses

import pytest

from xyz_agent_context.agent_framework.nexus_power.contracts.events import (
    TYPE_TEXT_DELTA,
    TYPE_TURN_DONE,
    Usage,
)
from xyz_agent_context.agent_framework.nexus_power.contracts.model import (
    ModelEvent,
    ModelParams,
    ProviderProfile,
)
from xyz_agent_context.agent_framework.nexus_power.assembly import LoopAssembly
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.harness.expression import (
    ExpressionContract,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.loop import (
    NexusPowerLoop,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.modeling.compaction import (
    ToolResultPruner,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.modeling.projector import (
    PassthroughProjector,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.session.error_classifier import (
    DefaultErrorClassifier,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.session.event_log import (
    NullEventLogWriter,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.session.turn_ledger import (
    TurnLedger,
)


class SlowStreamModel:
    """One endless-ish step: yields text deltas until aborted."""

    def __init__(self, deltas: int):
        self.profile = ProviderProfile(name="fake", context_window=100_000)
        self.yielded = 0
        self._deltas = deltas
        self.closed_early = False

    def estimate_cost_usd(self, usage, model):
        return None

    async def stream_step(self, request):
        try:
            for i in range(self._deltas):
                self.yielded += 1
                yield ModelEvent(kind="text_delta", payload={"text": f"t{i} "})
            yield ModelEvent(
                kind="done",
                payload={"stop_reason": "end_turn", "usage": Usage(input_tokens=5)},
            )
        finally:
            if self.yielded < self._deltas:
                self.closed_early = True


class NoTools:
    def visible_tools(self):
        return []

    def spec_for(self, name):
        return None

    async def execute(self, call):  # pragma: no cover - never dispatched here
        raise AssertionError("no tool should run in these tests")


class FlipAfter:
    """requested() returns False for the first n checks, then True."""

    def __init__(self, n: int):
        self._left = n

    def requested(self) -> bool:
        self._left -= 1
        return self._left < 0


def _assembly(model, cancel) -> LoopAssembly:
    return LoopAssembly(
        model=model,
        tools=NoTools(),
        projector=PassthroughProjector([{"role": "user", "content": "hi"}]),
        log=NullEventLogWriter(),
        cancel=cancel,
        expression=ExpressionContract(frozenset()),
        errors=DefaultErrorClassifier(),
        compaction=ToolResultPruner(),
        params=ModelParams(model="fake-model"),
    )


@pytest.mark.asyncio
async def test_cancel_mid_stream_aborts_provider_stream():
    model = SlowStreamModel(deltas=50)
    events = [
        e
        async for e in NexusPowerLoop(
            _assembly(model, FlipAfter(5)), TurnLedger("t1")
        ).run_turn()
    ]
    # The provider stream was closed early — not consumed to the end.
    assert model.closed_early
    assert model.yielded < 50
    done = [e for e in events if e.type == TYPE_TURN_DONE]
    assert len(done) == 1
    assert done[0].payload["end_reason"] == "INTERRUPTED"


@pytest.mark.asyncio
async def test_partial_monologue_survives_interrupt_in_ledger():
    """The deltas streamed before the abort must fold into the turn's
    assistant message — the interrupted work is history, not garbage."""
    model = SlowStreamModel(deltas=50)
    ledger = TurnLedger("t2")
    async for _ in NexusPowerLoop(_assembly(model, FlipAfter(6)), ledger).run_turn():
        pass
    folded = ledger.provider_messages()
    assistant = [m for m in folded if m.get("role") == "assistant"]
    assert assistant and assistant[0]["content"].startswith("t0 ")


@pytest.mark.asyncio
async def test_no_cancel_still_ends_naturally():
    class Never:
        def requested(self):
            return False

    model = SlowStreamModel(deltas=3)
    events = [
        e
        async for e in NexusPowerLoop(
            _assembly(model, Never()), TurnLedger("t3")
        ).run_turn()
    ]
    assert model.yielded == 3 and not model.closed_early
    done = [e for e in events if e.type == TYPE_TURN_DONE]
    assert done[0].payload["end_reason"] == "NO_MORE_ACTIONS"
    assert [e for e in events if e.type == TYPE_TEXT_DELTA]
