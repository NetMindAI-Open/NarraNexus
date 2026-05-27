"""
@file_name: test_helper_llm_fallback_decision.py
@author: Bin Liang
@date: 2026-05-12 (mode-aware refactor: 2026-05-25)
@description: Pins the decision logic of
``_should_run_helper_llm_fallback``.

Returns ``(mode, skip_reason)``:

- ``("no_reply", "")``: chat-triggered turn finished cleanly without
  send_message_to_user_directly → run helper_llm in "no_reply" mode,
  no error frame to surface.
- ``("after_error", "")``: chat-triggered turn hit a fatal mid-stream
  AND the agent had not sent any user-facing reply yet → run helper_llm
  in "after_error" mode with full context (system prompts + completed
  tool results + error info) and surface the original error as severity
  ``recovered`` after the recovery stream.
- ``("partial_reply_then_error", "")``: chat-triggered turn hit a fatal
  AFTER the agent already called send_message_to_user_directly → do
  NOT invoke helper_llm (the user already heard from the agent) but
  surface the truncated execution via severity
  ``recovered_after_reply``.
- ``(None, "non_chat_trigger" | "cancellation_requested" |
  "already_replied_via_tool")``: nothing to do (out of scope, user
  cancelled, or organic clean reply).
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


# ---------- mode: no_reply --------------------------------------------


def test_chat_with_no_reply_returns_no_reply_mode():
    mode, reason = _should_run_helper_llm_fallback(
        working_source="chat",
        agent_loop_response=[_non_reply_progress()],
        cancellation=None,
    )
    assert mode == "no_reply"
    assert reason == ""


def test_empty_response_on_chat_returns_no_reply_mode():
    """Edge case: agent loop produced literally nothing. Still chat,
    still no reply — no_reply mode runs."""
    mode, reason = _should_run_helper_llm_fallback(
        working_source="chat",
        agent_loop_response=[],
        cancellation=None,
    )
    assert mode == "no_reply"
    assert reason == ""


# ---------- mode: after_error ----------------------------------------


def test_fatal_error_with_no_prior_reply_returns_after_error_mode():
    """Was previously skipped with 'fatal_error_in_loop'; now triggers
    the after_error recovery path with full context."""
    fatal = ErrorMessage(
        error_message="Claude Code CLI did not respond for 90s",
        error_type="TimeoutError",
        severity="fatal",
    )
    mode, reason = _should_run_helper_llm_fallback(
        working_source="chat",
        agent_loop_response=[_non_reply_progress(), fatal],
        cancellation=None,
    )
    assert mode == "after_error"
    assert reason == ""


def test_legacy_severity_default_treated_as_fatal_for_mode():
    """Legacy ErrorMessage without explicit severity defaults to
    'fatal' → still triggers after_error mode."""
    legacy = ErrorMessage(error_message="boom", error_type="api_error")
    mode, _ = _should_run_helper_llm_fallback(
        working_source="chat",
        agent_loop_response=[legacy],
        cancellation=None,
    )
    assert mode == "after_error"


def test_recoverable_error_does_not_trigger_after_error_mode():
    """Recoverable errors are agent-visible info, not turn-killers — the
    agent continued normally. Mode stays 'no_reply' if no organic reply
    happened."""
    recoverable = ErrorMessage(
        error_message="rate_limit, retrying upstream",
        error_type="rate_limit",
        severity="recoverable",
    )
    mode, reason = _should_run_helper_llm_fallback(
        working_source="chat",
        agent_loop_response=[recoverable, _non_reply_progress()],
        cancellation=None,
    )
    assert mode == "no_reply"
    assert reason == ""


# ---------- mode: partial_reply_then_error ---------------------------


def test_fatal_after_organic_reply_returns_partial_reply_then_error():
    """Agent already spoke via send_message_to_user_directly, then a
    follow-up step crashed. Do NOT invoke helper_llm (we already
    replied), but surface the truncated execution via a recovered-
    after-reply badge."""
    fatal = ErrorMessage(
        error_message="follow-up tool exploded",
        error_type="RuntimeError",
        severity="fatal",
    )
    mode, reason = _should_run_helper_llm_fallback(
        working_source="chat",
        agent_loop_response=[_send_message_progress("real reply"), fatal],
        cancellation=None,
    )
    assert mode == "partial_reply_then_error"
    assert reason == ""


# ---------- skip: non-chat trigger ------------------------------------


@pytest.mark.parametrize("ws", ["message_bus", "job", "lark", "callback", "a2a"])
def test_non_chat_trigger_skips(ws):
    mode, reason = _should_run_helper_llm_fallback(
        working_source=ws,
        agent_loop_response=[_non_reply_progress()],
        cancellation=None,
    )
    assert mode is None
    assert reason == "non_chat_trigger"


def test_non_chat_trigger_with_fatal_still_skips():
    """Non-chat triggers have their own delivery semantics. Fatal on
    a job/lark turn must NOT spawn an after-error helper_llm reply."""
    fatal = ErrorMessage(error_message="x", error_type="X", severity="fatal")
    mode, reason = _should_run_helper_llm_fallback(
        working_source="job",
        agent_loop_response=[fatal],
        cancellation=None,
    )
    assert mode is None
    assert reason == "non_chat_trigger"


# ---------- skip: cancellation ----------------------------------------


def test_cancellation_skips_no_reply():
    cancellation = SimpleNamespace(is_cancelled=True)
    mode, reason = _should_run_helper_llm_fallback(
        working_source="chat",
        agent_loop_response=[_non_reply_progress()],
        cancellation=cancellation,
    )
    assert mode is None
    assert reason == "cancellation_requested"


def test_cancellation_skips_even_with_fatal_error():
    """User pressed stop. Don't burn helper_llm tokens recovering from
    something the user actively rejected."""
    cancellation = SimpleNamespace(is_cancelled=True)
    fatal = ErrorMessage(error_message="x", error_type="X", severity="fatal")
    mode, reason = _should_run_helper_llm_fallback(
        working_source="chat",
        agent_loop_response=[fatal],
        cancellation=cancellation,
    )
    assert mode is None
    assert reason == "cancellation_requested"


def test_cancellation_not_set_does_not_skip():
    cancellation = SimpleNamespace(is_cancelled=False)
    mode, _ = _should_run_helper_llm_fallback(
        working_source="chat",
        agent_loop_response=[_non_reply_progress()],
        cancellation=cancellation,
    )
    assert mode == "no_reply"


def test_no_cancellation_object_does_not_skip():
    mode, _ = _should_run_helper_llm_fallback(
        working_source="chat",
        agent_loop_response=[_non_reply_progress()],
        cancellation=None,
    )
    assert mode == "no_reply"


# ---------- skip: organic clean reply ---------------------------------


def test_send_message_present_skips():
    mode, reason = _should_run_helper_llm_fallback(
        working_source="chat",
        agent_loop_response=[_non_reply_progress(), _send_message_progress()],
        cancellation=None,
    )
    assert mode is None
    assert reason == "already_replied_via_tool"


def test_mcp_prefixed_send_message_recognised():
    """Tool name from MCP arrives as `mcp__chat_module__send_message_...`
    — substring match on `send_message_to_user_directly` must catch it."""
    mode, reason = _should_run_helper_llm_fallback(
        working_source="chat",
        agent_loop_response=[_send_message_progress()],
        cancellation=None,
    )
    assert mode is None
    assert reason == "already_replied_via_tool"
