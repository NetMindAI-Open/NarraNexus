"""
@file_name: test_fallback_skip_decision.py
@date: 2026-07-14
@description: Pins _fallback_skip_decision — the predicate that stops the
helper-LLM fallback from papering over a turn whose real cause a fabricated
reply would MASK.

Skip sub-cases:
  - "inline": response_processor already surfaced a fatal, actionable
    ErrorMessage (auth_expired / config_actionable) into agent_loop_response.
    Skip the fallback; no new message needed.
  - "raw_exception": the loop raised a Python exception so captured_error is
    set but no ErrorMessage exists yet. Skip the fallback AND emit an
    actionable one — otherwise the error is completely invisible. The third
    tuple element (target_error_type) tells the caller which copy to compose:
      * SELF_SERVICEABLE_ERROR_TYPE — user-fixable config (context window,
        credits, model id)
      * EXECUTOR_INFRA_ERROR_TYPE — platform-side executor infra (OOM kill,
        unreachable executor/broker)
A transient/generic failure returns (None, None, None) so the normal fallback
runs.
"""
from __future__ import annotations

from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
    _fallback_skip_decision,
)
from xyz_agent_context.schema import (
    AUTH_EXPIRED_ERROR_TYPE,
    ErrorMessage,
    SELF_SERVICEABLE_ERROR_TYPE,
    EXECUTOR_INFRA_ERROR_TYPE,
)


def _err(error_type: str, severity: str = "fatal") -> ErrorMessage:
    return ErrorMessage(
        error_message="x", error_type=error_type, severity=severity
    )


def test_inline_auth_expired_skips():
    kind, reason, target = _fallback_skip_decision(
        [_err(AUTH_EXPIRED_ERROR_TYPE)], None
    )
    assert kind == "inline"
    assert reason is None
    assert target is None


def test_inline_config_actionable_skips():
    kind, reason, target = _fallback_skip_decision(
        [_err(SELF_SERVICEABLE_ERROR_TYPE)], None
    )
    assert kind == "inline"
    assert reason is None
    assert target is None


def test_raw_exception_context_window_skips_with_reason():
    captured = {
        "error_type": "ContextWindowExceededError",
        "error_message": "inputs 75307 > 32769",
    }
    kind, reason, target = _fallback_skip_decision([], captured)
    assert kind == "raw_exception"
    assert reason == "context_window"
    assert target == SELF_SERVICEABLE_ERROR_TYPE


def test_raw_exception_unknown_type_but_message_signal_skips():
    """The inline `unknown` enum path also reaches here if it was raised as an
    exception carrying the litellm detail in the message."""
    captured = {
        "error_type": "unknown",
        "error_message": "This model's maximum context length is 8192 tokens",
    }
    kind, reason, target = _fallback_skip_decision([], captured)
    assert kind == "raw_exception"
    assert reason == "context_window"
    assert target == SELF_SERVICEABLE_ERROR_TYPE


def test_raw_exception_oom_sigkill_skips_as_infra():
    captured = {
        "error_type": "RuntimeError",
        "error_message": "Command failed with exit code -9",
    }
    kind, reason, target = _fallback_skip_decision([], captured)
    assert kind == "raw_exception"
    assert reason == "executor_oom"
    assert target == EXECUTOR_INFRA_ERROR_TYPE


def test_raw_exception_oom_sigabrt_skips_as_infra():
    captured = {
        "error_type": "RuntimeError",
        "error_message": "Command failed with exit code -6",
    }
    kind, reason, target = _fallback_skip_decision([], captured)
    assert kind == "raw_exception"
    assert reason == "executor_oom"
    assert target == EXECUTOR_INFRA_ERROR_TYPE


def test_raw_exception_executor_unreachable_skips_as_infra():
    captured = {
        "error_type": "ExecutorUnreachableError",
        "error_message": "Executor unreachable at http://nx-exec:8020",
    }
    kind, reason, target = _fallback_skip_decision([], captured)
    assert kind == "raw_exception"
    assert reason == "executor_unreachable"
    assert target == EXECUTOR_INFRA_ERROR_TYPE


def test_transient_captured_error_does_not_skip():
    captured = {"error_type": "TimeoutError", "error_message": "read timed out"}
    kind, reason, target = _fallback_skip_decision([], captured)
    assert kind is None
    assert reason is None
    assert target is None


def test_user_provider_connection_error_does_not_skip():
    """A USER's LLM-provider connection blip is transient — NOT executor infra.
    It must fall through to the normal fallback, not surface as infra fatal."""
    captured = {
        "error_type": "APIConnectionError",
        "error_message": "Connection error to api.openai.com",
    }
    kind, reason, target = _fallback_skip_decision([], captured)
    assert kind is None
    assert reason is None
    assert target is None


def test_no_error_does_not_skip():
    kind, reason, target = _fallback_skip_decision([], None)
    assert kind is None
    assert reason is None
    assert target is None


def test_non_fatal_recoverable_inline_does_not_trigger_inline_skip():
    """A recoverable rate-limit ErrorMessage is not a user-fixable fatal —
    it must NOT force an inline skip (the loop may still recover)."""
    kind, reason, target = _fallback_skip_decision(
        [_err("rate_limit_error", severity="recoverable")], None
    )
    assert kind is None
    assert reason is None
    assert target is None
