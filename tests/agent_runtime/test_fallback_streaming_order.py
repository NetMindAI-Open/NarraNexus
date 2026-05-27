"""
@file_name: test_fallback_streaming_order.py
@author: Bin Liang
@date: 2026-05-25
@description: Pins the streaming order of the post-agent-loop recovery
phase (``_stream_fallback_recovery``).

Why this contract matters:

The frontend renders messages strictly in the order they arrive. If the
ErrorMessage frame slips out BEFORE the helper_llm-generated reply
deltas, ``chatStore.ts`` briefly flips ``displayContent`` to the error
text before the synthetic send_message lands — a half-second of "system
broke" UX even when we recovered cleanly. So the recovery generator
must always yield in this order:

1. ``AgentTextDelta`` frames from the helper_llm stream (zero or more).
2. ``ProgressMessage`` synthesising a ``send_message_to_user_directly``
   tool call carrying ``details.reply_via`` (only if any content
   actually streamed).
3. ``ErrorMessage`` with the right severity (only if we captured a
   fatal upstream).

Severity is computed from outcome:
- ``"recovered"`` — fatal happened, helper_llm produced non-empty
  content. Frontend renders the reply normally + warning badge.
- ``"recovered_after_reply"`` — fatal happened AFTER the agent already
  spoke organically; helper_llm did NOT run. Frontend renders the
  agent's own reply + warning badge.
- ``"fatal"`` — fatal happened and helper_llm also failed / produced
  nothing. Frontend falls back to displaying the error directly.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import AsyncGenerator
from unittest.mock import patch

import pytest

from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
    _stream_fallback_recovery,
)
from xyz_agent_context.schema import (
    AgentTextDelta,
    ErrorMessage,
    ProgressMessage,
)


# ---------- helpers ----------------------------------------------------


async def _fake_helper_stream(deltas: list[str], raise_after: int | None = None):
    """Build an async generator that yields ``deltas`` then optionally
    raises mid-stream after ``raise_after`` chunks."""

    async def _gen(**_kwargs) -> AsyncGenerator[str, None]:
        for i, delta in enumerate(deltas):
            if raise_after is not None and i >= raise_after:
                raise RuntimeError("helper_llm exploded mid-stream")
            yield delta

    return _gen


async def _collect(gen):
    out = []
    async for msg in gen:
        out.append(msg)
    return out


# ---------- mode: no_reply (no error path) ----------------------------


@pytest.mark.asyncio
async def test_no_reply_mode_yields_deltas_then_synthetic_no_error():
    """Clean turn that just forgot to call send_message: helper_llm
    runs, we synthesize a tool call, no ErrorMessage at the tail."""
    helper = await _fake_helper_stream(["Hello ", "world"])
    with patch(
        "xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop._generate_fallback_reply_stream",
        new=helper,
    ):
        msgs = await _collect(_stream_fallback_recovery(
            fallback_mode="no_reply",
            captured_error=None,
            context_messages=[],
            agent_loop_response=[],
            final_output="",
            user_input="hi",
            cancellation=None,
            db=None,
            agent_id="a1",
        ))

    types = [type(m).__name__ for m in msgs]
    assert types == ["AgentTextDelta", "AgentTextDelta", "ProgressMessage"]
    # Synthetic message carries the right reply_via tag.
    synth = msgs[-1]
    assert synth.details["reply_via"] == "helper_llm_no_reply"
    assert synth.details["arguments"]["content"] == "Hello world"
    # No ErrorMessage at the tail.
    assert not any(isinstance(m, ErrorMessage) for m in msgs)


# ---------- mode: after_error (recovered) -----------------------------


@pytest.mark.asyncio
async def test_after_error_yields_deltas_then_synthetic_then_recovered_error():
    """Fatal hit mid-loop, helper_llm recovered: deltas + synthetic +
    ErrorMessage(severity=recovered), strictly in that order."""
    helper = await _fake_helper_stream(["Recovered ", "reply."])
    with patch(
        "xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop._generate_fallback_reply_stream",
        new=helper,
    ):
        msgs = await _collect(_stream_fallback_recovery(
            fallback_mode="after_error",
            captured_error={"error_type": "TimeoutError", "error_message": "boom"},
            context_messages=[],
            agent_loop_response=[],
            final_output="some reasoning",
            user_input="hi",
            cancellation=None,
            db=None,
            agent_id="a1",
        ))

    types = [type(m).__name__ for m in msgs]
    assert types == [
        "AgentTextDelta", "AgentTextDelta", "ProgressMessage", "ErrorMessage"
    ]
    synth = msgs[2]
    assert synth.details["reply_via"] == "helper_llm_after_error"
    assert synth.details["arguments"]["content"] == "Recovered reply."
    err = msgs[3]
    assert err.severity == "recovered"
    assert err.error_type == "TimeoutError"
    assert "boom" in err.error_message


# ---------- mode: after_error (fallback also failed) ------------------


@pytest.mark.asyncio
async def test_after_error_fallback_also_fails_yields_fatal_error():
    """Fatal + helper_llm raised before producing anything → no synthetic
    reply, ErrorMessage stays severity=fatal so frontend shows it."""
    helper = await _fake_helper_stream([], raise_after=0)
    with patch(
        "xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop._generate_fallback_reply_stream",
        new=helper,
    ):
        msgs = await _collect(_stream_fallback_recovery(
            fallback_mode="after_error",
            captured_error={"error_type": "TimeoutError", "error_message": "boom"},
            context_messages=[],
            agent_loop_response=[],
            final_output="",
            user_input="hi",
            cancellation=None,
            db=None,
            agent_id="a1",
        ))

    types = [type(m).__name__ for m in msgs]
    assert types == ["ErrorMessage"]
    assert msgs[0].severity == "fatal"


@pytest.mark.asyncio
async def test_after_error_partial_stream_then_fail_marks_partial():
    """helper_llm yielded some content then died: we still synthesize
    the partial reply (don't lose what we have), but the severity stays
    'recovered' since the user did get a usable reply."""
    # Two-element deltas so iteration index 1 actually hits the raise.
    helper = await _fake_helper_stream(["Partial ", "unreachable"], raise_after=1)
    with patch(
        "xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop._generate_fallback_reply_stream",
        new=helper,
    ):
        msgs = await _collect(_stream_fallback_recovery(
            fallback_mode="after_error",
            captured_error={"error_type": "TimeoutError", "error_message": "boom"},
            context_messages=[],
            agent_loop_response=[],
            final_output="",
            user_input="hi",
            cancellation=None,
            db=None,
            agent_id="a1",
        ))

    types = [type(m).__name__ for m in msgs]
    assert types == ["AgentTextDelta", "ProgressMessage", "ErrorMessage"]
    synth = msgs[1]
    assert synth.details["arguments"]["content"] == "Partial"
    assert synth.details.get("fallback_partial") is True
    err = msgs[2]
    # Got partial content → still counts as recovered for the user.
    assert err.severity == "recovered"


# ---------- mode: partial_reply_then_error ----------------------------


@pytest.mark.asyncio
async def test_partial_reply_then_error_skips_helper_yields_error_only():
    """Agent already spoke; helper_llm must NOT run. Only the
    ErrorMessage(severity=recovered_after_reply) is yielded so the
    frontend surfaces a warning badge alongside the organic reply."""
    # If helper_llm IS called this will raise loudly.
    def _should_not_be_called(**_kwargs):
        raise AssertionError(
            "helper_llm must not run in partial_reply_then_error mode"
        )

    async def _bad(**kwargs):
        _should_not_be_called(**kwargs)
        yield ""  # never reached

    with patch(
        "xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop._generate_fallback_reply_stream",
        new=_bad,
    ):
        msgs = await _collect(_stream_fallback_recovery(
            fallback_mode="partial_reply_then_error",
            captured_error={"error_type": "RuntimeError", "error_message": "post-reply boom"},
            context_messages=[],
            agent_loop_response=[],
            final_output="",
            user_input="hi",
            cancellation=None,
            db=None,
            agent_id="a1",
        ))

    types = [type(m).__name__ for m in msgs]
    assert types == ["ErrorMessage"]
    assert msgs[0].severity == "recovered_after_reply"
    assert msgs[0].error_type == "RuntimeError"


# ---------- mode: None (nothing to do) -------------------------------


@pytest.mark.asyncio
async def test_no_mode_no_error_yields_nothing():
    """Skip path — no helper_llm, no error to surface."""
    msgs = await _collect(_stream_fallback_recovery(
        fallback_mode=None,
        captured_error=None,
        context_messages=[],
        agent_loop_response=[],
        final_output="",
        user_input="hi",
        cancellation=None,
        db=None,
        agent_id="a1",
    ))
    assert msgs == []


# ---------- cancellation aborts the fallback ------------------------


@pytest.mark.asyncio
async def test_cancellation_mid_stream_aborts_fallback():
    """If cancellation trips while helper_llm is streaming, we stop
    yielding more deltas. Any partial content already produced is still
    synthesised (don't lose it)."""
    cancellation = SimpleNamespace(is_cancelled=False)

    async def _flipping_helper(**_kwargs):
        yield "first "
        cancellation.is_cancelled = True
        yield "second "  # this should be dropped
        yield "third"

    with patch(
        "xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop._generate_fallback_reply_stream",
        new=_flipping_helper,
    ):
        msgs = await _collect(_stream_fallback_recovery(
            fallback_mode="no_reply",
            captured_error=None,
            context_messages=[],
            agent_loop_response=[],
            final_output="",
            user_input="hi",
            cancellation=cancellation,
            db=None,
            agent_id="a1",
        ))

    deltas = [m for m in msgs if isinstance(m, AgentTextDelta)]
    assert [d.delta for d in deltas] == ["first "]
    # Synthetic still emitted with the partial content.
    synths = [m for m in msgs if isinstance(m, ProgressMessage)]
    assert len(synths) == 1
    assert synths[0].details["arguments"]["content"] == "first"
