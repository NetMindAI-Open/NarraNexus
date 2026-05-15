"""
@file_name: test_helper_llm_fallback_decision.py
@author: Bin Liang
@date: 2026-05-12
@description: Pins the four "skip" conditions of the chat no-reply
helper_llm fallback (step_3_agent_loop._should_run_helper_llm_fallback).

Each condition guards a distinct failure mode:

1. Non-chat trigger → out of scope. message_bus / job / lark have their
   own reply pathways (or deliberately don't reply); firing the
   fallback there would either spam, loop agents, or impersonate
   background work.

2. Fatal error in agent_loop_response → agent loop crashed mid-turn
   (TimeoutError, SDK crash). state.final_output is incomplete
   reasoning; asking helper_llm to summarise that hallucinates a reply
   from a half-thought. chat_module's failed-turn path is the correct
   handler instead.

3. Cancellation requested → user pressed stop. Honouring the token is
   the whole point. Firing the fallback after stop burns helper_llm
   tokens for a reply the user explicitly rejected.

4. Already replied via send_message_to_user_directly → nothing to
   recover; the agent did its job.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
    _should_run_helper_llm_fallback,
)
from xyz_agent_context.schema import (
    ErrorMessage,
    ProgressMessage,
    ProgressStatus,
)


# ---------- helpers ----------------------------------------------------


def _send_message_progress(content: str = "hi") -> ProgressMessage:
    return ProgressMessage(
        step="3.4.1",
        title="Tool call",
        description="send_message_to_user_directly",
        status=ProgressStatus.COMPLETED,
        details={
            "tool_name": "mcp__chat_module__send_message_to_user_directly",
            "arguments": {"content": content},
        },
    )


def _non_reply_progress() -> ProgressMessage:
    return ProgressMessage(
        step="3.4.1",
        title="Tool call",
        description="get_chat_history",
        status=ProgressStatus.COMPLETED,
        details={
            "tool_name": "mcp__chat_module__get_chat_history",
            "arguments": {"instance_id": "chat_x"},
        },
    )


# ---------- happy path -------------------------------------------------


def test_chat_with_no_reply_runs_fallback():
    should_run, reason = _should_run_helper_llm_fallback(
        working_source="chat",
        agent_loop_response=[_non_reply_progress()],
        cancellation=None,
    )
    assert should_run is True
    assert reason == ""


def test_empty_response_on_chat_runs_fallback():
    """Edge case: agent loop produced literally nothing (no tool calls,
    no errors). Still chat-triggered, still no reply — fallback runs."""
    should_run, reason = _should_run_helper_llm_fallback(
        working_source="chat",
        agent_loop_response=[],
        cancellation=None,
    )
    assert should_run is True
    assert reason == ""


# ---------- skip: non-chat trigger ------------------------------------


@pytest.mark.parametrize("ws", ["message_bus", "job", "lark", "callback", "a2a"])
def test_non_chat_trigger_skips_fallback(ws):
    should_run, reason = _should_run_helper_llm_fallback(
        working_source=ws,
        agent_loop_response=[_non_reply_progress()],
        cancellation=None,
    )
    assert should_run is False
    assert reason == "non_chat_trigger"


# ---------- skip: fatal error in stream -------------------------------


def test_fatal_error_message_skips_fallback():
    fatal = ErrorMessage(
        error_message="Claude Code CLI did not respond for 90s",
        error_type="TimeoutError",
        severity="fatal",
    )
    should_run, reason = _should_run_helper_llm_fallback(
        working_source="chat",
        agent_loop_response=[_non_reply_progress(), fatal],
        cancellation=None,
    )
    assert should_run is False
    assert reason == "fatal_error_in_loop"


def test_recoverable_error_does_not_skip_fallback():
    """Recoverable errors (rate-limit blip etc.) must NOT block the
    fallback — they are agent-visible information, not turn-killers."""
    recoverable = ErrorMessage(
        error_message="rate_limit, retrying upstream",
        error_type="rate_limit",
        severity="recoverable",
    )
    should_run, reason = _should_run_helper_llm_fallback(
        working_source="chat",
        agent_loop_response=[recoverable, _non_reply_progress()],
        cancellation=None,
    )
    assert should_run is True
    assert reason == ""


def test_error_message_without_explicit_severity_defaults_fatal():
    """Backwards-compat: legacy ErrorMessage without severity field is
    treated as fatal, matching ErrorMessage's default."""
    legacy = ErrorMessage(error_message="boom", error_type="api_error")
    should_run, _ = _should_run_helper_llm_fallback(
        working_source="chat",
        agent_loop_response=[legacy],
        cancellation=None,
    )
    assert should_run is False


# ---------- skip: cancellation ----------------------------------------


def test_cancellation_token_skips_fallback():
    cancellation = SimpleNamespace(is_cancelled=True)
    should_run, reason = _should_run_helper_llm_fallback(
        working_source="chat",
        agent_loop_response=[_non_reply_progress()],
        cancellation=cancellation,
    )
    assert should_run is False
    assert reason == "cancellation_requested"


def test_cancellation_not_set_does_not_skip():
    cancellation = SimpleNamespace(is_cancelled=False)
    should_run, _ = _should_run_helper_llm_fallback(
        working_source="chat",
        agent_loop_response=[_non_reply_progress()],
        cancellation=cancellation,
    )
    assert should_run is True


def test_no_cancellation_object_does_not_skip():
    """RunContext may not always have a cancellation token attached;
    `None` must not crash the decision."""
    should_run, _ = _should_run_helper_llm_fallback(
        working_source="chat",
        agent_loop_response=[_non_reply_progress()],
        cancellation=None,
    )
    assert should_run is True


# ---------- skip: already replied -------------------------------------


def test_send_message_present_skips_fallback():
    should_run, reason = _should_run_helper_llm_fallback(
        working_source="chat",
        agent_loop_response=[_non_reply_progress(), _send_message_progress()],
        cancellation=None,
    )
    assert should_run is False
    assert reason == "already_replied_via_tool"


def test_mcp_prefixed_send_message_recognised():
    """Tool name from MCP arrives as `mcp__chat_module__send_message_...` —
    substring match on `send_message_to_user_directly` must catch it."""
    should_run, reason = _should_run_helper_llm_fallback(
        working_source="chat",
        agent_loop_response=[_send_message_progress()],
        cancellation=None,
    )
    assert should_run is False
    assert reason == "already_replied_via_tool"


# ---------- precedence: fatal beats already-replied ------------------


def test_fatal_error_takes_precedence_over_already_replied():
    """If a fatal error showed up, we should NOT pretend the turn was
    fine just because the agent emitted a send_message before crashing
    (the assistant content from that pre-crash call might be partial /
    misleading). chat_module's failed-turn path takes over."""
    fatal = ErrorMessage(
        error_message="SDK crashed",
        error_type="RuntimeError",
        severity="fatal",
    )
    should_run, reason = _should_run_helper_llm_fallback(
        working_source="chat",
        agent_loop_response=[_send_message_progress("partial..."), fatal],
        cancellation=None,
    )
    assert should_run is False
    assert reason == "fatal_error_in_loop"
