"""
@file_name: test_fallback_prompt_assembly.py
@author: Bin Liang
@date: 2026-05-25
@description: Pins the contract of _serialize_agent_loop_for_prompt — the
helper that renders agent_loop_response (raw runtime frames) into a
plain-text "what happened this turn so far" section for the helper_llm
fallback prompt.

The helper has three core duties:

1. **Render every meaningful frame in chronological order** so the
   fallback LLM can reason about what the agent attempted. Tool calls,
   tool outputs, assistant text deltas, thinking, and errors are all
   in scope.

2. **Cap per-entry size** (default 4 KB) so a single oversized tool
   result can't dominate the prompt. Truncated entries get a clear
   marker.

3. **Cap total size** (default 32 KB) so the prompt never blows out
   the helper_llm context window. When total exceeds cap, drop oldest
   entries first — recent activity is more useful to the recovery
   reply than ancient setup work.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
    _serialize_agent_loop_for_prompt,
)
from xyz_agent_context.schema import (
    AgentTextDelta,
    AgentThinking,
    ErrorMessage,
    ProgressMessage,
    ProgressStatus,
)


# ---------- helpers ----------------------------------------------------


def _tool_call(name: str, args: dict, step: str = "3.4.1") -> ProgressMessage:
    return ProgressMessage(
        step=step,
        title=f"Tool: {name}",
        description="Executing...",
        status=ProgressStatus.RUNNING,
        details={"tool_name": name, "arguments": args},
    )


def _tool_output(output: str, step: str = "3.4.1") -> ProgressMessage:
    return ProgressMessage(
        step=step,
        title="Tool result",
        description="✓ Execution completed",
        status=ProgressStatus.COMPLETED,
        details={"output": output},
    )


# ---------- empty / trivial cases --------------------------------------


def test_empty_response_returns_empty_marker():
    """Empty agent_loop_response → caller decides what to do with the
    section; we return a stable sentinel rather than the empty string so
    the prompt builder can distinguish 'no activity' from 'missing
    field'."""
    out = _serialize_agent_loop_for_prompt([])
    assert out == "(no activity recorded)"


def test_single_tool_call_renders_name_and_args():
    out = _serialize_agent_loop_for_prompt([
        _tool_call("search_database", {"query": "users"}),
    ])
    assert "search_database" in out
    assert "users" in out
    assert "[tool_call]" in out


def test_tool_call_followed_by_output_pair():
    """Adjacent tool_call + tool_output are both rendered, in order."""
    out = _serialize_agent_loop_for_prompt([
        _tool_call("fetch_data", {"id": 42}),
        _tool_output("{\"name\":\"Alice\"}"),
    ])
    assert "fetch_data" in out
    assert "Alice" in out
    # call must appear before output
    assert out.index("fetch_data") < out.index("Alice")


def test_assistant_text_deltas_rendered():
    out = _serialize_agent_loop_for_prompt([
        AgentTextDelta(delta="Hello "),
        AgentTextDelta(delta="world"),
    ])
    assert "Hello world" in out  # deltas concatenated
    assert "[assistant_text]" in out


def test_thinking_rendered():
    out = _serialize_agent_loop_for_prompt([
        AgentThinking(thinking_content="Let me check the database"),
    ])
    assert "Let me check the database" in out
    assert "[thinking]" in out


def test_error_rendered_with_severity():
    out = _serialize_agent_loop_for_prompt([
        ErrorMessage(
            error_message="Connection refused",
            error_type="NetworkError",
            severity="fatal",
        ),
    ])
    assert "Connection refused" in out
    assert "NetworkError" in out
    assert "[error]" in out


# ---------- truncation -------------------------------------------------


def test_oversized_tool_result_tail_truncated():
    """Per-entry cap of 4 KB: a 10 KB output gets truncated with a
    clear marker so the LLM knows content was dropped."""
    big = "X" * 10_000
    out = _serialize_agent_loop_for_prompt(
        [_tool_output(big)],
        max_per_entry=4096,
    )
    # truncation marker present
    assert "[truncated" in out
    # the rendered entry itself fits within max_per_entry + marker overhead.
    # Generous upper bound: 4 KB content + 200 bytes structural framing.
    assert len(out) <= 4096 + 500


def test_oversized_tool_call_args_tail_truncated():
    """Same cap applies to args, not just outputs."""
    huge_arg = {"blob": "Y" * 10_000}
    out = _serialize_agent_loop_for_prompt(
        [_tool_call("upload", huge_arg)],
        max_per_entry=4096,
    )
    assert "[truncated" in out
    assert len(out) <= 4096 + 500


def test_total_cap_drops_oldest_first():
    """When summed entries exceed max_total, oldest entries drop first.
    Recent activity is more useful for the recovery reply.

    Zero-padded tags avoid the substring trap ("tool_0" is a substring
    of "tool_10"). Padded output bodies make the cap actually bite."""
    entries = []
    payload = "PAD" * 60  # ~180 chars per output
    for i in range(20):
        entries.append(_tool_call(f"calltag{i:02d}", {"n": i}))
        entries.append(_tool_output(f"outputtag{i:02d}_{payload}"))
    out = _serialize_agent_loop_for_prompt(
        entries, max_per_entry=4096, max_total=2000
    )
    assert len(out) <= 2000 + 500  # cap + small structural marker overhead
    # most-recent entries survive
    assert "calltag19" in out
    assert "outputtag19" in out
    # oldest entries dropped
    assert "calltag00" not in out
    assert "outputtag00" not in out
    # dropped-prefix marker tells the LLM context is incomplete
    assert "earlier activity omitted" in out.lower()


def test_render_order_preserved():
    """Frames are interleaved (thinking → call → output → text → error
    is a realistic timeline). The serializer must preserve order
    exactly — the LLM relies on causal sequence to write the recovery
    reply."""
    out = _serialize_agent_loop_for_prompt([
        AgentThinking(thinking_content="thinking_A"),
        _tool_call("call_B", {}),
        _tool_output("output_C"),
        AgentTextDelta(delta="text_D"),
        ErrorMessage(error_message="error_E", error_type="X"),
    ])
    positions = [out.index(s) for s in [
        "thinking_A", "call_B", "output_C", "text_D", "error_E",
    ]]
    assert positions == sorted(positions)
