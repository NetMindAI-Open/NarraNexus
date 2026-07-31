"""
@file_name: test_response_processor_pending_tool.py
@date: 2026-07-30
@description: Pending tool_call frames (name-first, arguments still
streaming) must reach the frontend with the replacement key, without
polluting execution state.

The frontend replaces a pending row in place when the completed call
lands, keyed by ``details.tool_call_id`` — so the completed frame must
carry it too. A pending frame must NOT record_tool_call (the completed
call does; recording both would double the step count and the persisted
timeline). And a pending frame for a user-reply tool is dropped
entirely: its reply already streams live via reply deltas, and an
empty-argument reply frame would inject a stray empty bubble.
"""
from __future__ import annotations

from xyz_agent_context.agent_runtime.execution_state import ExecutionState
from xyz_agent_context.agent_runtime.response_processor import (
    ResponseProcessor,
    ResponseType,
)


def _tool_call_event(tool_name: str, *, pending: bool) -> dict:
    return {
        "type": "run_item_stream_event",
        "item": {
            "type": "tool_call_item",
            "tool_name": tool_name,
            "tool_call_id": "call_1",
            "arguments": {} if pending else {"command": "ls"},
            **({"pending": True} if pending else {}),
        },
    }


def test_pending_tool_call_ships_key_and_flag_without_state_update():
    rp = ResponseProcessor()
    state = ExecutionState()
    events = list(rp._handle_run_item_stream_event(
        _tool_call_event("bash", pending=True), state,
    ))
    assert len(events) == 1
    ev = events[0]
    assert ev.type == ResponseType.TOOL_CALL
    assert ev.message.details["tool_call_id"] == "call_1"
    assert ev.message.details["pending"] is True
    # The completed call is the one that records; recording the pending
    # frame too would double-count the step.
    assert ev.state_update is None


def test_completed_tool_call_carries_the_replacement_key():
    rp = ResponseProcessor()
    state = ExecutionState()
    events = list(rp._handle_run_item_stream_event(
        _tool_call_event("bash", pending=False), state,
    ))
    assert len(events) == 1
    ev = events[0]
    assert ev.message.details["tool_call_id"] == "call_1"
    assert not ev.message.details["pending"]
    assert ev.state_update is not None
    assert ev.state_update["method"] == "record_tool_call"


def test_pending_reply_tool_frame_is_dropped():
    rp = ResponseProcessor()
    state = ExecutionState()
    events = list(rp._handle_run_item_stream_event(
        _tool_call_event(
            "mcp__chat_module__send_message_to_user_directly", pending=True,
        ),
        state,
    ))
    assert events == []
