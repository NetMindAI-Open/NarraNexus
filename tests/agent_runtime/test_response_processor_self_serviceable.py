"""
@file_name: test_response_processor_self_serviceable.py
@date: 2026-07-14
@description: ResponseProcessor must classify DETERMINISTIC, user-self-
serviceable failures (context window too small, no credits, bad model id) as
fatal + actionable — NOT as generic "recoverable" API errors.

Incident (the "black box" P1): a NetMind user on a 32k model failed every
turn with `litellm.ContextWindowExceededError: inputs 75307 > 32769`, which
the Claude CLI collapsed to the enum `unknown`. The old handler tagged it
`severity="recoverable"`, so the turn kept going and step_3's no_reply
fallback fabricated a DeepSeek reply — masking a failure the user could fix
by switching models. These tests pin the new behaviour: such errors become
`severity="fatal"`, `error_type="config_actionable"`, carry an
`action_reason`, and give actionable guidance. Genuine transient errors stay
`recoverable`; auth still wins (it's checked first).
"""
from __future__ import annotations

from xyz_agent_context.agent_runtime.execution_state import ExecutionState
from xyz_agent_context.agent_runtime.response_processor import ResponseProcessor
from xyz_agent_context.schema import (
    AUTH_EXPIRED_ERROR_TYPE,
    ErrorMessage,
    SELF_SERVICEABLE_ERROR_TYPE,
)


def _error_event(error_message: str, error_type: str) -> dict:
    return {
        "type": "raw_response_event",
        "data": {
            "type": "response.error",
            "error_message": error_message,
            "error_type": error_type,
        },
    }


def _process(event: dict) -> ErrorMessage:
    rp = ResponseProcessor()
    state = ExecutionState()
    results = list(rp.process(event, state))
    msgs = [r.message for r in results if isinstance(r.message, ErrorMessage)]
    assert len(msgs) == 1
    return msgs[0]


def test_context_window_becomes_fatal_config_actionable():
    """The real incident payload: type collapsed to `unknown`, the litellm
    context-window detail only in the message."""
    msg = _process(_error_event(
        "litellm.ContextWindowExceededError: inputs tokens + max_new_tokens "
        "must be <= 32769. Given: 75307 inputs tokens and 32000 max_new_tokens",
        "unknown",
    ))
    assert msg.error_type == SELF_SERVICEABLE_ERROR_TYPE
    assert msg.severity == "fatal"
    assert msg.action_reason == "context_window"
    # Actionable + shows the concrete numbers, not a black-box "unknown".
    assert "context window" in msg.error_message.lower()
    assert "75307" in msg.error_message


def test_insufficient_balance_becomes_fatal_config_actionable():
    msg = _process(_error_event(
        "You have insufficient balance to use this model", "unknown"
    ))
    assert msg.error_type == SELF_SERVICEABLE_ERROR_TYPE
    assert msg.severity == "fatal"
    assert msg.action_reason == "insufficient_balance"


def test_bad_model_id_becomes_fatal_config_actionable():
    msg = _process(_error_event("The model `gpt-9` does not exist", "unknown"))
    assert msg.error_type == SELF_SERVICEABLE_ERROR_TYPE
    assert msg.severity == "fatal"
    assert msg.action_reason == "model_not_found"


def test_transient_error_stays_recoverable():
    """A rate-limit blip is neither auth nor self-serviceable — stays
    recoverable so the loop keeps assembling its reply."""
    msg = _process(_error_event("429 too many requests", "rate_limit_error"))
    assert msg.error_type == "rate_limit_error"
    assert msg.severity == "recoverable"


def test_auth_takes_precedence_over_self_serviceable():
    """Auth is checked first; a message that mentions both auth and other
    signals must resolve to auth (re-login), not config_actionable."""
    msg = _process(_error_event("401 unauthorized: invalid api key", "api_error"))
    assert msg.error_type == AUTH_EXPIRED_ERROR_TYPE
    assert msg.severity == "fatal"


def test_generic_error_stays_recoverable():
    """The residual bucket (our-own bug / unattributable) is untouched."""
    msg = _process(_error_event("some unexpected internal error", "Exception"))
    assert msg.error_type == "Exception"
    assert msg.severity == "recoverable"
