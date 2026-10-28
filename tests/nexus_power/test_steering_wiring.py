"""
@file_name: test_steering_wiring.py
@author: Bin Liang
@date: 2026-08-21
@description: The framework entry points forward a steering inlet down to
the LoopAssembly. QueueSteeringInlet's drain behaviour is proven at the
loop level in test_loop_e2e; here we pin only the wiring — that a caller
of run_turn_events / serve_turn can supply an inlet and it reaches the
loop (default None still mounts NullSteeringInlet). A transport layer is
what will actually feed the inlet; that is a separate change.
"""

import asyncio
import json

import pytest

from narranexus_plugins.frameworks_nexus_power.core import assembly as assembly_mod
from narranexus_plugins.frameworks_nexus_power.core.assembly import (
    TurnRequest,
    run_turn_events,
)
from narranexus_plugins.frameworks_nexus_power.core.contracts.events import EndReason
from narranexus_plugins.frameworks_nexus_power.core.contracts.options import TurnOptions
from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.harness.steering import (
    NullSteeringInlet,
    QueueSteeringInlet,
)


class _NeverCancelled:
    def requested(self) -> bool:
        return False


def _install_capturing_loop(monkeypatch) -> dict:
    """Replace the real loop + model client with light fakes so the test
    exercises run_turn_events' wiring without a provider call. Returns a
    dict the fake loop writes the mounted inlet into.

    Depends on run_turn_events importing NexusPowerLoop / LiteLLMModelClient
    lazily inside the function: patching the source modules works only
    because the names are re-read per call. Hoisting those imports to
    module top in assembly.py would defeat this fixture (see assembly.py's
    docstring)."""
    captured: dict = {}

    class _CapturingLoop:
        def __init__(self, assembly, ledger):
            captured["steering"] = assembly.steering
            captured["tools"] = [s.name for s in assembly.tools.visible_tools()]
            self._ledger = ledger

        async def run_turn(self):
            yield self._ledger.close_turn(EndReason.NO_MORE_ACTIONS, model="fake")

    import narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.loop as loop_mod
    import narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.modeling.model_client as mc_mod
    import narranexus.platform.agent_framework.llm.litellm_client as lc_mod

    monkeypatch.setattr(loop_mod, "NexusPowerLoop", _CapturingLoop)
    monkeypatch.setattr(mc_mod, "LiteLLMModelClient", lambda profile, client: object())
    monkeypatch.setattr(lc_mod, "LitellmClient", lambda *a, **k: object())
    return captured


def _request(steerable: bool = False) -> TurnRequest:
    # cwd here is inert: both callers run through run_turn_events with
    # log=None (NullEventLogWriter, no disk). The serve_turn test, which
    # does construct a real log file, drives serve_turn from its own JSON
    # payload with a tmp_path cwd instead.
    return TurnRequest(
        thread_id="t1",
        messages=[{"role": "user", "content": "hi"}],
        options=TurnOptions(
            cwd="/tmp", agent_id="a", model="fake-model", provider="anthropic",
            steerable=steerable,
        ),
    )


async def _drain(gen) -> None:
    async for _ in gen:
        pass


@pytest.mark.asyncio
async def test_run_turn_events_mounts_the_supplied_steering_inlet(monkeypatch):
    captured = _install_capturing_loop(monkeypatch)
    inlet = QueueSteeringInlet(asyncio.Queue())

    await _drain(run_turn_events(_request(), _NeverCancelled(), steering=inlet))

    assert captured["steering"] is inlet


@pytest.mark.asyncio
async def test_run_turn_events_defaults_to_the_null_inlet(monkeypatch):
    captured = _install_capturing_loop(monkeypatch)

    await _drain(run_turn_events(_request(), _NeverCancelled()))

    # Default (no steering arg) still mounts the always-empty inlet — the
    # behaviour every existing turn relies on.
    assert isinstance(captured["steering"], NullSteeringInlet)


@pytest.mark.asyncio
async def test_wait_tool_is_hidden_on_a_non_steerable_run_even_with_an_inlet_mounted(monkeypatch):
    # The C1 regression, on the PRODUCTION arm. runner.main() mounts a
    # QueueSteeringInlet on EVERY turn, so the wait tool cannot be gated on
    # "is an inlet present" (always true) — it is gated on TurnOptions.steerable.
    # Here: a real inlet IS mounted, but the run is NOT steerable → wait_for_input
    # must NOT be visible (nothing could ever feed that inlet; the tool would only
    # block up to 300s). Flip the gate to `steering is None` and this goes green
    # falsely while prod exposes the tool on every turn.
    captured = _install_capturing_loop(monkeypatch)
    inlet = QueueSteeringInlet(asyncio.Queue())  # mounted, exactly like runner.main()

    await _drain(run_turn_events(_request(steerable=False), _NeverCancelled(), steering=inlet))

    assert captured["steering"] is inlet  # the inlet IS mounted (drain still works)
    assert "wait_for_input" not in captured["tools"]  # …but the wait tool is hidden


@pytest.mark.asyncio
async def test_wait_tool_is_exposed_on_a_steerable_run(monkeypatch):
    # The other arm: a steerable run (a producer can feed the inlet) DOES expose
    # wait_for_input. Together with the test above this pins the gate to the flag,
    # not to inlet identity.
    captured = _install_capturing_loop(monkeypatch)
    inlet = QueueSteeringInlet(asyncio.Queue())

    await _drain(run_turn_events(_request(steerable=True), _NeverCancelled(), steering=inlet))

    assert "wait_for_input" in captured["tools"]


@pytest.mark.asyncio
async def test_serve_turn_forwards_steering_to_run_turn_events(monkeypatch, tmp_path):
    captured = _install_capturing_loop(monkeypatch)
    inlet = QueueSteeringInlet(asyncio.Queue())

    from narranexus_plugins.frameworks_nexus_power.core.runner import serve_turn

    payload = json.dumps({
        "thread_id": "t1",
        "messages": [{"role": "user", "content": "hi"}],
        # tmp_path keeps the turn's .nexus_power/ log file out of a shared dir.
        "options": {"cwd": str(tmp_path), "agent_id": "a", "model": "fake-model", "provider": "anthropic"},
    })
    lines: list = []

    async def _collect(obj) -> None:
        lines.append(obj)

    rc = await serve_turn(payload, _collect, steering=inlet)

    assert captured["steering"] is inlet
    # The forward reached the loop AND the serve completed cleanly — the
    # exit line is output_mode-independent, so it pins success without
    # depending on the event-row shape.
    assert rc == 0
    assert lines[-1] == {"exit": {"ok": True}}
