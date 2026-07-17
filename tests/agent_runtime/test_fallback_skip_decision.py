"""
@file_name: test_fallback_skip_decision.py
@date: 2026-07-14
@description: Pins _fallback_skip_decision — the predicate that stops the
helper-LLM fallback from papering over a turn the USER must fix.

Two skip sub-cases must be distinguished:
  - "inline": response_processor already surfaced a fatal, actionable
    ErrorMessage (auth_expired / config_actionable) into agent_loop_response.
    Skip the fallback; no new message needed.
  - "raw_exception": the loop raised a Python exception (e.g.
    ContextWindowExceededError) so captured_error is set but no ErrorMessage
    exists yet. Skip the fallback AND emit an actionable one — otherwise the
    error is completely invisible.
A transient/generic failure returns (None, None) so the normal fallback runs.
"""
from __future__ import annotations

from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
    _fallback_skip_decision,
)
from xyz_agent_context.schema import (
    AUTH_EXPIRED_ERROR_TYPE,
    ErrorMessage,
    SELF_SERVICEABLE_ERROR_TYPE,
)


def _err(error_type: str, severity: str = "fatal") -> ErrorMessage:
    return ErrorMessage(
        error_message="x", error_type=error_type, severity=severity
    )


def test_inline_auth_expired_skips():
    kind, reason = _fallback_skip_decision(
        [_err(AUTH_EXPIRED_ERROR_TYPE)], None
    )
    assert kind == "inline"
    assert reason is None


def test_inline_config_actionable_skips():
    kind, reason = _fallback_skip_decision(
        [_err(SELF_SERVICEABLE_ERROR_TYPE)], None
    )
    assert kind == "inline"
    assert reason is None


def test_raw_exception_context_window_skips_with_reason():
    captured = {
        "error_type": "ContextWindowExceededError",
        "error_message": "inputs 75307 > 32769",
    }
    kind, reason = _fallback_skip_decision([], captured)
    assert kind == "raw_exception"
    assert reason == "context_window"


def test_raw_exception_unknown_type_but_message_signal_skips():
    """The inline `unknown` enum path also reaches here if it was raised as an
    exception carrying the litellm detail in the message."""
    captured = {
        "error_type": "unknown",
        "error_message": "This model's maximum context length is 8192 tokens",
    }
    kind, reason = _fallback_skip_decision([], captured)
    assert kind == "raw_exception"
    assert reason == "context_window"


def test_transient_captured_error_does_not_skip():
    captured = {"error_type": "TimeoutError", "error_message": "read timed out"}
    kind, reason = _fallback_skip_decision([], captured)
    assert kind is None
    assert reason is None


def test_no_error_does_not_skip():
    kind, reason = _fallback_skip_decision([], None)
    assert kind is None
    assert reason is None


def test_non_fatal_recoverable_inline_does_not_trigger_inline_skip():
    """A recoverable rate-limit ErrorMessage is not a user-fixable fatal —
    it must NOT force an inline skip (the loop may still recover)."""
    kind, reason = _fallback_skip_decision(
        [_err("rate_limit_error", severity="recoverable")], None
    )
    assert kind is None
    assert reason is None
