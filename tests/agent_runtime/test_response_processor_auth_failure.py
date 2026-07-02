"""
@file_name: test_response_processor_auth_failure.py
@date: 2026-06-11
@description: ResponseProcessor must classify coding-agent credential
failures as fatal + actionable, not as generic "recoverable" API errors.

Incident 2026-06-11: a used codex OAuth refresh token surfaced as a
``response.error`` with ``error_type="unauthorized"`` (and a "log out and
sign in again" message). The old handler tagged EVERY response.error as
``severity="recoverable"``, so the turn kept going and step_3's no_reply
fallback fabricated a gpt-5 reply — masking the dead login. These tests
pin the new behaviour: auth failures become ``severity="fatal"`` with
``error_type="auth_expired"`` and an actionable re-login message, while
genuinely transient errors stay ``recoverable``.
"""
from __future__ import annotations

from xyz_agent_context.agent_runtime.execution_state import ExecutionState
from xyz_agent_context.agent_runtime.response_processor import (
    AUTH_EXPIRED_ERROR_TYPE,
    ResponseProcessor,
    _is_auth_failure,
)
from xyz_agent_context.schema import ErrorMessage


def _error_event(error_message: str, error_type: str) -> dict:
    return {
        "type": "raw_response_event",
        "data": {
            "type": "response.error",
            "error_message": error_message,
            "error_type": error_type,
        },
    }


def _process(event: dict):
    rp = ResponseProcessor()
    state = ExecutionState()
    results = list(rp.process(event, state))
    msgs = [r.message for r in results if isinstance(r.message, ErrorMessage)]
    assert len(msgs) == 1
    return msgs[0]


# ---------------- _is_auth_failure unit ----------------------------


def test_is_auth_failure_matches_category_and_phrases():
    assert _is_auth_failure("unauthorized", "")
    assert _is_auth_failure("authentication_error", "")
    assert _is_auth_failure("", "Please log out and sign in again.")
    assert _is_auth_failure("api_error", "HTTP 401 Unauthorized")
    assert _is_auth_failure("", "your refresh token was already used")


def test_is_auth_failure_ignores_transient_errors():
    assert not _is_auth_failure("rate_limit_error", "429 too many requests")
    assert not _is_auth_failure("turn.failed", "model produced no output")
    assert not _is_auth_failure("server_error", "internal error")


def test_invalid_request_error_is_not_auth_by_type_alone():
    """``invalid_request_error`` is OpenAI's catch-all client-error type;
    keying auth on it alone misfired non-auth 400s (context length, bad
    model, content policy) into a fatal 're-login'. These must stay
    non-auth so the turn can still recover / use the helper fallback."""
    assert not _is_auth_failure(
        "invalid_request_error",
        "This model's maximum context length is 200000 tokens.",
    )
    assert not _is_auth_failure("invalid_request_error", "Unknown model: gpt-9")
    assert not _is_auth_failure(
        "invalid_request_error", "Your input was blocked by content policy."
    )


def test_openai_bad_key_still_classified_by_message():
    """Removing the bare type must NOT regress a genuine bad OpenAI key —
    its message wording ('Incorrect API key provided') is caught by phrase."""
    assert _is_auth_failure(
        "invalid_request_error", "Incorrect API key provided: sk-***"
    )


# ---------------- classification through process() ------------------


def test_codex_unauthorized_becomes_fatal_auth_expired():
    """The real incident payload: codex_error_info='unauthorized' surfaced
    as error_type='unauthorized' by the translator."""
    msg = _process(_error_event(
        "Your access token could not be refreshed because your refresh "
        "token was already used. Please log out and sign in again.",
        "unauthorized",
    ))
    assert msg.error_type == AUTH_EXPIRED_ERROR_TYPE
    assert msg.severity == "fatal"
    # Actionable: tells the user how to recover.
    assert "codex login" in msg.error_message.lower()


def test_message_only_auth_signal_still_classified():
    """Even when error_type is generic, a clear auth message must be
    caught (other providers only signal in the human message)."""
    msg = _process(_error_event(
        "401 unauthorized: invalid api key", "api_error"
    ))
    assert msg.error_type == AUTH_EXPIRED_ERROR_TYPE
    assert msg.severity == "fatal"


def test_transient_error_stays_recoverable():
    """A rate-limit blip is NOT an auth failure — must stay recoverable so
    the loop keeps assembling its reply."""
    msg = _process(_error_event("429 too many requests", "rate_limit_error"))
    assert msg.error_type == "rate_limit_error"
    assert msg.severity == "recoverable"
