"""
@file_name: test_tool_output_call_id.py
@date: 2026-07-29
@description: ``tool_output`` steps must carry the id of the call they answer.

Why this matters beyond tidiness: the transcript we hand the CLI on every turn
(see the 2026-07-29 synthetic-transcript plan) rebuilds ``tool_use`` /
``tool_result`` pairs out of ``events.event_log``, and the Anthropic message
format pairs them strictly BY ID. Today the log stores no id on the output side,
so pairing can only be positional — the Nth output belongs to the Nth call.

That assumption is already wrong for parallel tool calls. ``response_processor``
itself documents the shape: with parallel calls every ``tool_call`` arrives
before any output, and outputs then come back in completion order, not call
order. A positional rebuild therefore attaches results to the wrong calls, and a
mis-paired ``tool_result`` is an API 400 on every following turn rather than a
degraded answer.

So the id has to be recorded at the source. These tests pin: it is stored when
the event supplies one, it stays absent (not fabricated) when the event does
not, and the existing positional display lookup keeps working either way.
"""
from __future__ import annotations

from xyz_agent_context.agent_runtime.execution_state import ExecutionState


def test_tool_output_records_the_call_id():
    state = ExecutionState().record_tool_output("done", tool_call_id="call_abc123")
    step = state.all_steps[-1]
    assert step["type"] == "tool_output"
    assert step["output"] == "done"
    assert step["tool_call_id"] == "call_abc123"


def test_tool_output_without_an_id_stores_empty_not_a_guess():
    """A driver that does not report ids must not get a fabricated one — the
    rebuild needs to know it has to fall back to positional pairing."""
    state = ExecutionState().record_tool_output("done")
    assert state.all_steps[-1]["tool_call_id"] == ""


def test_ids_survive_out_of_order_completion():
    """The parallel-call case the positional scheme cannot express: two calls
    issued in order, results returning reversed."""
    state = ExecutionState()
    state = state.record_tool_call("first_tool", "call_1", {"a": 1})
    state = state.record_tool_call("second_tool", "call_2", {"b": 2})
    state = state.record_tool_output("result of two", tool_call_id="call_2")
    state = state.record_tool_output("result of one", tool_call_id="call_1")

    outputs = [s for s in state.all_steps if s["type"] == "tool_output"]
    assert [o["tool_call_id"] for o in outputs] == ["call_2", "call_1"]
    # Positional pairing would have mapped these to call_1 then call_2 — i.e.
    # both results attached to the wrong call.
    assert outputs[0]["output"] == "result of two"


def test_recording_output_stays_immutable():
    """Same contract as every other recorder here: a new object, original
    untouched."""
    original = ExecutionState()
    updated = original.record_tool_output("x", tool_call_id="call_x")
    assert original.all_steps == ()
    assert original.tool_output_count == 0
    assert updated.tool_output_count == 1
    assert len(updated.all_steps) == 1
