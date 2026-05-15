"""
@file_name: test_response_processor_thinking_coalesce.py
@author: Bin Liang
@date: 2026-05-13
@description: Integration tests for ResponseProcessor + _ThinkingBatcher.

Verifies:
  * Content is preserved verbatim across thousands of single-char chunks
  * Output frame count drops by orders of magnitude
  * thinking → tool_call ordering is preserved (residual flush before
    non-thinking event)
  * flush_pending emits residual at stream end
"""
from __future__ import annotations

import time

import pytest

from xyz_agent_context.agent_runtime.execution_state import ExecutionState
from xyz_agent_context.agent_runtime.response_processor import (
    ResponseProcessor,
    ResponseType,
)
from xyz_agent_context.schema import AgentThinking, ProgressMessage


def _thinking_event(content: str) -> dict:
    return {
        "type": "run_item_stream_event",
        "item": {"type": "thinking_item", "content": content},
    }


def _tool_call_event(name: str = "Bash") -> dict:
    return {
        "type": "run_item_stream_event",
        "item": {
            "type": "tool_call_item",
            "tool_name": name,
            "tool_call_id": "tc_abc",
            "arguments": {"command": "ls"},
        },
    }


def test_5000_one_char_chunks_coalesce_to_far_fewer_frames():
    """The Xiong scenario: DeepSeek emits one ZH-char-per-token. Verify
    we drop frame count by orders of magnitude without losing content."""
    processor = ResponseProcessor()
    state = ExecutionState()

    source = "想" * 5000  # 5000 chars, 1 per chunk
    emitted_thinking_payloads: list[str] = []

    for ch in source:
        for result in processor.process(_thinking_event(ch), state):
            state = processor.apply_state_update(state, result)
            if result.type == ResponseType.THINKING:
                assert isinstance(result.message, AgentThinking)
                emitted_thinking_payloads.append(result.message.thinking_content)

    # End of stream — drain residual
    for result in processor.flush_pending(state):
        state = processor.apply_state_update(state, result)
        if result.type == ResponseType.THINKING:
            assert isinstance(result.message, AgentThinking)
            emitted_thinking_payloads.append(result.message.thinking_content)

    # Verbatim preservation — every character makes it through, in order
    assert "".join(emitted_thinking_payloads) == source

    # Frame count reduction — 5000 input chunks should produce far fewer
    # output frames. The ≥500-char trigger means at least 5000/500=10
    # forced flushes, plus time-based flushes; in practice somewhere
    # between 10 and ~100. We assert "less than 200" to leave plenty of
    # headroom while still failing if coalescing didn't work.
    assert len(emitted_thinking_payloads) < 200, (
        f"expected <200 frames after coalescing, got {len(emitted_thinking_payloads)}"
    )
    # Sanity: at least one frame must have been emitted
    assert emitted_thinking_payloads, "no frames emitted at all"


def test_tool_call_flushes_residual_thinking_in_order():
    """Critical ordering invariant: when a tool_call arrives mid-thinking,
    the buffered thinking MUST be flushed FIRST so the UI sees the
    natural chronological sequence."""
    processor = ResponseProcessor()
    state = ExecutionState()

    emitted: list[tuple[ResponseType, object]] = []

    # 50 small thinking chunks — below 500 char threshold, no time
    # threshold either (synchronous loop, all within 100 ms)
    for _ in range(50):
        for result in processor.process(_thinking_event("a"), state):
            state = processor.apply_state_update(state, result)
            if result.message is not None:
                emitted.append((result.type, result.message))

    # At this point the buffer holds 50 chars of "a"; nothing has been
    # emitted yet (no chars/time trigger fired). Now a tool_call arrives.
    for result in processor.process(_tool_call_event(), state):
        state = processor.apply_state_update(state, result)
        if result.message is not None:
            emitted.append((result.type, result.message))

    # Order: THINKING first (residual flush), then TOOL_CALL
    assert len(emitted) == 2
    assert emitted[0][0] == ResponseType.THINKING
    assert isinstance(emitted[0][1], AgentThinking)
    assert emitted[0][1].thinking_content == "a" * 50
    assert emitted[1][0] == ResponseType.TOOL_CALL
    assert isinstance(emitted[1][1], ProgressMessage)


def test_flush_pending_at_stream_end_emits_residual():
    """The agent_loop wrapper MUST call flush_pending after the receive
    loop ends; otherwise residual chunks are silently dropped."""
    processor = ResponseProcessor()
    state = ExecutionState()

    for ch in "hello":
        for result in processor.process(_thinking_event(ch), state):
            state = processor.apply_state_update(state, result)

    # Nothing emitted yet (under 500 chars, under 100 ms)
    # Drain at end-of-stream
    residual_emitted: list[AgentThinking] = []
    for result in processor.flush_pending(state):
        state = processor.apply_state_update(state, result)
        if result.type == ResponseType.THINKING:
            residual_emitted.append(result.message)

    assert len(residual_emitted) == 1
    assert residual_emitted[0].thinking_content == "hello"


def test_flush_pending_idempotent_when_buffer_empty():
    """Calling flush_pending twice (or on empty buffer) is a no-op."""
    processor = ResponseProcessor()
    state = ExecutionState()

    assert list(processor.flush_pending(state)) == []

    # Buffer some content, drain
    list(processor.process(_thinking_event("x" * 600), state))  # forces flush
    # Now empty
    assert list(processor.flush_pending(state)) == []
