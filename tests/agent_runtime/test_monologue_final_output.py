"""
@file_name: test_monologue_final_output.py
@author: Bin Liang
@date: 2026-07-29
@description: NexusPower monologue must reach ExecutionState.final_output.

The monologue/expression contract maps the framework's assistant text to
``thinking_item`` for DISPLAY (the user watches the agent think, never
sees monologue as an answer). But ``final_output`` is the platform's
reasoning channel: ChatModule persists it as ``meta_data.reasoning`` and
splices it into next turn's history as ``<my_reasoning>``, and the
helper-fallback decision reads it. Before this fix, nexus turns left
``final_output`` empty — the cross-turn value-carry contract ("restate
device codes / job ids / URLs in your reasoning") silently never fired.

The fix: ``thinking_item`` events carrying ``monologue: true`` (stamped
only by the NexusPower LegacyEventAdapter for text deltas) accumulate
into ``final_output`` while still displaying as thinking. Provider CoT
(``thinking_delta`` — no flag) must NOT enter ``final_output``, matching
the claude driver where ThinkingBlocks never reach it either.
"""
from __future__ import annotations

from xyz_agent_context.agent_runtime.execution_state import ExecutionState
from xyz_agent_context.agent_runtime.response_processor import (
    ResponseProcessor,
    ResponseType,
)


def _thinking_event(content: str, *, monologue: bool | None = None) -> dict:
    item: dict = {"type": "thinking_item", "content": content}
    if monologue is not None:
        item["monologue"] = monologue
    return {"type": "run_item_stream_event", "item": item}


def _drain(processor: ResponseProcessor, state: ExecutionState, event: dict):
    for result in processor.process(event, state):
        state = processor.apply_state_update(state, result)
    return state


def _finish(processor: ResponseProcessor, state: ExecutionState):
    for result in processor.flush_pending(state):
        state = processor.apply_state_update(state, result)
    return state


def test_monologue_chunks_accumulate_into_final_output():
    processor = ResponseProcessor()
    state = ExecutionState()
    for chunk in ("The job id ", "is job_42; ", "restating for next turn."):
        state = _drain(processor, state, _thinking_event(chunk, monologue=True))
    state = _finish(processor, state)
    assert state.final_output == "The job id is job_42; restating for next turn."


def test_provider_cot_never_enters_final_output():
    """thinking_delta (DeepSeek reasoning_content etc.) has no monologue
    flag — parity with claude, whose ThinkingBlocks never reach
    final_output either."""
    processor = ResponseProcessor()
    state = ExecutionState()
    state = _drain(processor, state, _thinking_event("private CoT " * 50))
    state = _finish(processor, state)
    assert state.final_output == ""


def test_interleaved_cot_and_monologue_keeps_only_monologue_in_order():
    processor = ResponseProcessor()
    state = ExecutionState()
    state = _drain(processor, state, _thinking_event("cot-a ", monologue=False))
    state = _drain(processor, state, _thinking_event("mono-1 ", monologue=True))
    state = _drain(processor, state, _thinking_event("cot-b ", monologue=False))
    state = _drain(processor, state, _thinking_event("mono-2", monologue=True))
    state = _finish(processor, state)
    assert state.final_output == "mono-1 mono-2"


def test_display_stream_still_carries_everything_verbatim():
    """Iron rule #16: the fix must not change what the user sees — the
    thinking display keeps BOTH monologue and CoT, verbatim, in order."""
    processor = ResponseProcessor()
    state = ExecutionState()
    seen: list[str] = []
    for content, mono in (("cot ", False), ("mono ", True), ("tail", False)):
        for result in processor.process(
            _thinking_event(content, monologue=mono), state
        ):
            state = processor.apply_state_update(state, result)
            if result.type == ResponseType.THINKING:
                seen.append(result.message.thinking_content)
    for result in processor.flush_pending(state):
        state = processor.apply_state_update(state, result)
        if result.type == ResponseType.THINKING:
            seen.append(result.message.thinking_content)
    assert "".join(seen) == "cot mono tail"


def test_monologue_survives_flush_at_tool_call_boundary():
    """Residual flush (non-thinking item arriving) must carry the pending
    monologue too — not only the stream-end flush."""
    processor = ResponseProcessor()
    state = ExecutionState()
    state = _drain(processor, state, _thinking_event("before tool. ", monologue=True))
    tool_event = {
        "type": "run_item_stream_event",
        "item": {
            "type": "tool_call_item",
            "tool_name": "Bash",
            "tool_call_id": "tc_1",
            "arguments": {"command": "ls"},
        },
    }
    state = _drain(processor, state, tool_event)
    state = _drain(processor, state, _thinking_event("after tool.", monologue=True))
    state = _finish(processor, state)
    assert state.final_output == "before tool. after tool."
