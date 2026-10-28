"""
@file_name: test_wait_for_input.py
@author: Bin Liang
@date: 2026-08-23
@description: The `wait_for_input` control tool, end to end.

Two layers:
* WaitChannel — records a clamped wait request on the shared WaitState and
  never blocks (the loop owns the blocking wait).
* Loop WAIT boundary — when the agent asked to wait, the loop blocks on the
  steering inlet up to the requested seconds: input arrived (a message arrives -> it
  rides the next model step) or timeout (nothing -> a wrap-up notice rides the
  next step). A requested wait beats the turn-closing stop.

Delete the loop WAIT block (or WaitChannel's state write) and the loop tests
here go red — a mute step with a pending wait would close in one request
instead of blocking for a second.
"""

import asyncio

import pytest

from narranexus_plugins.frameworks_nexus_power.core.assembly import (
    LoopAssembly,
    _steer_channels,
)
from narranexus_plugins.frameworks_nexus_power.core.contracts.events import Usage
from narranexus_plugins.frameworks_nexus_power.core.contracts.model import (
    ModelEvent,
    ModelParams,
    ProviderProfile,
)
from narranexus_plugins.frameworks_nexus_power.core.contracts.tooling import (
    ToolCall,
    ToolContext,
    ToolResult,
    ToolSpec,
)
from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.harness.expression import (
    ExpressionContract,
)
from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.harness.steering import (
    NullSteeringInlet,
    QueueSteeringInlet,
)
from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.loop import (
    NexusPowerLoop,
)
from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.modeling.compaction import (
    ToolResultPruner,
)
from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.modeling.projector import (
    PassthroughProjector,
)
from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.session.error_classifier import (
    DefaultErrorClassifier,
)
from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.session.event_log import (
    NullEventLogWriter,
)
from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.session.turn_ledger import (
    TurnLedger,
)
from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.tooling.wait_channel import (
    DEFAULT_WAIT_SECONDS,
    MAX_WAIT_SECONDS,
    MIN_WAIT_SECONDS,
    WaitChannel,
    WaitState,
)


# --------------------------------------------------------------------------
# WaitChannel — unit
# --------------------------------------------------------------------------


class _Ctx:
    pass


@pytest.mark.asyncio
async def test_wait_channel_records_the_clamped_request_and_never_blocks():
    state = WaitState()
    chan = WaitChannel(state)
    res = await asyncio.wait_for(
        chan.call("wait_for_input", {"seconds": 12}, _Ctx()), timeout=0.5
    )
    assert res.ok
    assert state.pending == 12.0  # recorded for the loop to consume


def test_wait_state_request_is_the_single_clamp_home():
    # The clamp lives in ONE place — WaitState.request — so every producer
    # (WaitChannel today, the executor's /steer tomorrow) is bounded without
    # re-clamping. Exercise it directly: request() both returns and stores the
    # clamped value, and the loop can trust `pending` is in-range.
    state = WaitState()
    assert state.request(99999) == MAX_WAIT_SECONDS and state.pending == MAX_WAIT_SECONDS
    assert state.request(0) == MIN_WAIT_SECONDS and state.pending == MIN_WAIT_SECONDS
    assert state.request(None) == DEFAULT_WAIT_SECONDS  # missing → default
    assert state.request("nonsense") == DEFAULT_WAIT_SECONDS  # garbage → default
    assert state.request(float("nan")) == DEFAULT_WAIT_SECONDS  # NaN → default


@pytest.mark.asyncio
async def test_wait_channel_clamps_via_wait_state():
    # The channel is a thin recorder over WaitState.request — confirm the tool
    # path reaches the same clamp (not a second, drifting one).
    state = WaitState()
    chan = WaitChannel(state)

    await chan.call("wait_for_input", {"seconds": 99999}, _Ctx())
    assert state.pending == MAX_WAIT_SECONDS
    await chan.call("wait_for_input", {"seconds": 0}, _Ctx())
    assert state.pending == MIN_WAIT_SECONDS
    await chan.call("wait_for_input", {}, _Ctx())  # missing → default
    assert state.pending == DEFAULT_WAIT_SECONDS


@pytest.mark.asyncio
async def test_wait_channel_exposes_the_tool_and_rejects_others():
    chan = WaitChannel(WaitState())
    names = [s.name for s in chan.list_tools()]
    assert names == ["wait_for_input"]
    bad = await chan.call("nope", {}, _Ctx())
    assert not bad.ok


# --------------------------------------------------------------------------
# Wiring guards — a source-less inlet's wait is an instant, non-blocking [],
# and the wait tool is exposed ONLY on a steerable turn.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_null_inlet_wait_returns_empty_at_once_and_never_blocks():
    # No producer can ever feed NullSteeringInlet, so a wait on it must not
    # burn the timeout — it returns [] immediately. Guarding this stops a
    # regression where a non-steerable turn that somehow reached WAIT would
    # hang a worker for the full requested seconds. wait_for(0.1) proves the
    # 300s max never even starts ticking.
    inlet = NullSteeringInlet()
    got = await asyncio.wait_for(inlet.wait_for_input(300.0, _NeverCancel()), timeout=0.1)
    assert got == []
    assert inlet.take_consumed() == []  # nothing arrived → nothing to ack


def test_steer_channels_are_gated_on_the_steerable_flag():
    # The wait tool appears ONLY on a STEERABLE run. The gate is the explicit
    # TurnOptions.steerable flag — NOT whether an inlet object is mounted, because
    # the subprocess runner mounts one on every turn (see the production-arm test
    # in test_steering_wiring). On a non-steerable run wait_for_input could only
    # block on a queue nothing will feed, so it must be absent. Assert both arms.
    assert _steer_channels(False, WaitState()) == ()  # not steerable → tool absent
    exposed = _steer_channels(True, WaitState())
    assert [s.name for chan in exposed for s in chan.list_tools()] == ["wait_for_input"]


# --------------------------------------------------------------------------
# Loop WAIT boundary — e2e against a scripted fake model
# --------------------------------------------------------------------------


class _FakeModel:
    def __init__(self, steps):
        self._steps = list(steps)
        self.profile = ProviderProfile(name="fake", context_window=1000)
        self.requests = []

    def estimate_cost_usd(self, usage, model):
        return 0.0

    async def stream_step(self, request):
        self.requests.append(request)
        for ev in self._steps.pop(0):
            yield ev


class _WaitTools:
    """Executes `wait_for_input` by setting the shared WaitState (what the real
    WaitChannel does), so this exercises the LOOP's wait handling."""

    def __init__(self, wait_state, secs):
        self._wait_state = wait_state
        self._secs = secs
        self.executed = []

    def visible_tools(self):
        return [ToolSpec(name="wait_for_input", description="", input_schema={})]

    def model_tools(self):
        return self.visible_tools()

    def spec_for(self, name):
        return next((s for s in self.visible_tools() if s.name == name), None)

    async def execute(self, call: ToolCall) -> ToolResult:
        self.executed.append(call)
        if call.name == "wait_for_input":
            # Go through request() — the SINGLE clamp door — exactly like the real
            # WaitChannel, so no test bypasses the bound (a raw `pending = 0.05`
            # would smuggle a sub-MIN value the loop then trusts). secs below MIN
            # (0.05) is clamped up to 1.0s here; the e2e assertions are on request
            # counts / injected content, not on sub-second timing, so this only
            # costs ~1s of wall-clock, never correctness.
            self._wait_state.request(self._secs)
        return ToolResult(call_id=call.id, ok=True, content="waiting")


class _NeverCancel:
    def requested(self):
        return False


class _TrippableCancel:
    def __init__(self):
        self._tripped = False

    def trip(self):
        self._tripped = True

    def requested(self):
        return self._tripped


def _use_wait(cid="w1"):
    return ModelEvent(
        kind="tool_use", content_index=0,
        payload={"call_id": cid, "tool_name": "wait_for_input", "args": {}},
    )


def _done(stop="end_turn"):
    return ModelEvent(kind="done", payload={
        "stop_reason": stop, "usage": Usage(input_tokens=5, output_tokens=1)})


def _assembly(model, tools, steering, wait_state, cancel=None):
    return LoopAssembly(
        model=model,
        tools=tools,
        projector=PassthroughProjector([{"role": "user", "content": "hi"}]),
        log=NullEventLogWriter(),
        cancel=cancel or _NeverCancel(),
        expression=ExpressionContract(frozenset()),
        errors=DefaultErrorClassifier(),
        compaction=ToolResultPruner(),
        params=ModelParams(model="fake-model"),
        steering=steering,
        wait=wait_state,
    )


async def _run(assembly):
    ledger = TurnLedger("t1")
    return [e async for e in NexusPowerLoop(assembly, ledger).run_turn()], ledger


@pytest.mark.asyncio
async def test_wait_blocks_then_a_late_message_rides_the_next_step():
    # input arrived: the agent calls wait_for_input; nothing is queued at the boundary
    # (so DRAIN_STEERING does NOT pre-empt the wait); a message arrives DURING
    # the wait and rides the next model request.
    queue: asyncio.Queue = asyncio.Queue()
    wait_state = WaitState()
    model = _FakeModel([
        [_use_wait(), _done()],          # step 1: ask to wait
        [ModelEvent(kind="text_delta", payload={"text": "got it"}), _done()],  # step 2
    ])
    tools = _WaitTools(wait_state, secs=5.0)

    async def _late():
        await asyncio.sleep(0.05)
        await queue.put({"role": "user", "content": "LATE: a teammate replied"})

    producer = asyncio.create_task(_late())
    events, _ = await asyncio.wait_for(
        _run(_assembly(model, tools, QueueSteeringInlet(queue), wait_state)),
        timeout=3.0,
    )
    await producer

    assert len(model.requests) == 2  # the wait forced a second step
    injected = [
        m for m in model.requests[1].messages
        if "LATE:" in str(m.get("content", ""))
    ]
    assert injected, "the message that arrived during the wait must ride the next step"


@pytest.mark.asyncio
async def test_a_same_step_message_satisfies_the_wait_and_no_stale_wait_blocks_later():
    # I1 guard: the agent asks to wait, but a message is ALREADY queued at that
    # step's boundary. DRAIN takes it and continues — the wait is satisfied
    # (it asked to wait FOR input; input arrived same step). The request must
    # then be GONE: a later mute step must close at once, never block on the
    # stale intent. secs is huge (300s) so if the read-and-clear regressed, the
    # second step would block on wait_for_input and blow the 1s wall-clock.
    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait({"role": "user", "content": "SAME-STEP: already here"})
    wait_state = WaitState()
    model = _FakeModel([
        [_use_wait(), _done()],  # step 1: ask to wait — but a msg is already queued
        [ModelEvent(kind="text_delta", payload={"text": "done"}), _done()],  # step 2: mute
    ])
    tools = _WaitTools(wait_state, secs=300.0)  # would hang a full 300s if it carried

    events, _ = await asyncio.wait_for(
        _run(_assembly(model, tools, QueueSteeringInlet(queue), wait_state)),
        timeout=1.0,  # the whole run must finish fast; a stale wait would time out
    )
    assert len(model.requests) == 2
    injected = [
        m for m in model.requests[1].messages
        if "SAME-STEP:" in str(m.get("content", ""))
    ]
    assert injected, "the same-step message must ride the next step (drained, not waited)"
    assert wait_state.pending is None  # read-and-cleared, not left armed


@pytest.mark.asyncio
async def test_wait_cancelled_mid_wait_skips_the_notice_and_interrupts():
    # The cancel-driven empty return path through the real loop: cancel trips
    # WHILE waiting → wait_for_input returns [] → the loop must NOT inject the
    # timeout notice (the `elif not a.cancel.requested()`) and must let the
    # top-of-loop cancel boundary interrupt cleanly.
    queue: asyncio.Queue = asyncio.Queue()  # stays empty
    wait_state = WaitState()
    model = _FakeModel([[_use_wait(), _done()]])  # only step 1; interrupt before a 2nd
    tools = _WaitTools(wait_state, secs=30.0)  # long wait so cancel wins
    cancel = _TrippableCancel()

    async def _trip():
        await asyncio.sleep(0.05)
        cancel.trip()

    tripper = asyncio.create_task(_trip())
    events, _ = await asyncio.wait_for(
        _run(_assembly(model, tools, QueueSteeringInlet(queue), wait_state, cancel)),
        timeout=3.0,
    )
    await tripper

    # No wait_timed_out notice was injected (cancel skipped it), and the turn
    # interrupted after the single step rather than taking a second.
    assert len(model.requests) == 1
    assert not any(
        "wrap up this turn" in str(m.get("content", "")).lower()
        for r in model.requests for m in r.messages
    )


@pytest.mark.asyncio
async def test_wait_consumption_emits_a_steer_consumed_event():
    # Integration with the consumption contract: a steered message (with
    # _steer_id) consumed via the WAIT path must emit a steer_consumed event, so
    # a message steered into a WAITING run advances the producer's cursor exactly
    # like DRAIN_STEERING.
    from narranexus_plugins.frameworks_nexus_power.core.contracts.events import (
        TYPE_STEER_CONSUMED,
    )

    queue: asyncio.Queue = asyncio.Queue()
    wait_state = WaitState()
    model = _FakeModel([
        [_use_wait(), _done()],
        [ModelEvent(kind="text_delta", payload={"text": "ok"}), _done()],
    ])
    tools = _WaitTools(wait_state, secs=5.0)

    async def _late():
        await asyncio.sleep(0.05)
        await queue.put({"role": "user", "content": "steered", "_steer_id": "m9"})

    producer = asyncio.create_task(_late())
    events, _ = await asyncio.wait_for(
        _run(_assembly(model, tools, QueueSteeringInlet(queue), wait_state)),
        timeout=3.0,
    )
    await producer

    consumed = [e for e in events if getattr(e, "type", None) == TYPE_STEER_CONSUMED]
    assert consumed, "the WAIT path must report consumption"
    assert consumed[0].payload["ids"] == ["m9"]
    # and _steer_id never reached the model request
    assert all("_steer_id" not in m for m in model.requests[1].messages)


@pytest.mark.asyncio
async def test_wait_times_out_and_injects_a_wrap_up_notice():
    # timeout: nothing arrives within the (tiny, loop-trusted) wait window, so the
    # next step carries the wrap-up notice — the agent is told, not left hanging.
    queue: asyncio.Queue = asyncio.Queue()
    wait_state = WaitState()
    model = _FakeModel([
        [_use_wait(), _done()],
        [ModelEvent(kind="text_delta", payload={"text": "wrapping up"}), _done()],
    ])
    tools = _WaitTools(wait_state, secs=0.05)  # clamped up to MIN(1.0s) by request(); assert is on the notice, not timing

    events, _ = await asyncio.wait_for(
        _run(_assembly(model, tools, QueueSteeringInlet(queue), wait_state)),
        timeout=3.0,
    )

    assert len(model.requests) == 2
    notice = [
        m for m in model.requests[1].messages
        if "no new message arrived" in str(m.get("content", ""))
    ]
    assert notice, "a timed-out wait must tell the agent so it can wrap up"


@pytest.mark.asyncio
async def test_a_pending_wait_beats_the_turn_closing_stop():
    # Control: the same mute step WITHOUT a pending wait closes in one request
    # (see the null-steering e2e). With a pending wait it must NOT close on step
    # one — it blocks, times out, and takes a second step. This isolates the
    # WAIT block as the cause, not the harness.
    wait_state = WaitState()
    model = _FakeModel([
        [_use_wait(), _done()],
        [ModelEvent(kind="text_delta", payload={"text": "done"}), _done()],
    ])
    tools = _WaitTools(wait_state, secs=0.05)  # clamped up to MIN(1.0s) by request(); assert is on step count
    events, _ = await asyncio.wait_for(
        _run(_assembly(model, tools, QueueSteeringInlet(asyncio.Queue()), wait_state)),
        timeout=3.0,
    )
    assert len(model.requests) == 2  # did not close on the first stoppable step
