"""
@file_name: test_inline_assistant_error_event.py
@date: 2026-07-14
@description: An inline AssistantMessage.error must fold CLI stderr into the
error event so the real provider cause survives.

AssistantMessage.error is a 6-value enum; the Claude CLI collapses a litellm
ContextWindowExceededError to `unknown`, and the token-count detail survives
ONLY in CLI stderr. _inline_assistant_error_event keeps error_type = the enum
(so the classifier can still key on it) and appends the stderr tail so
classify_self_serviceable can recover "context window" from the message — the
end-to-end fix for the "black box" P1.
"""
from xyz_agent_context.agent_framework.xyz_claude_agent_sdk import (
    _inline_assistant_error_event,
)
from xyz_agent_context.agent_framework.llm_failure import classify_self_serviceable


def _data(ev):
    return ev["data"]


def test_event_has_response_error_shape_and_keeps_enum():
    ev = _inline_assistant_error_event("unknown", ["some stderr"])
    assert ev["type"] == "raw_response_event"
    assert ev["data"]["type"] == "response.error"
    # error_type stays the raw enum so the classifier can still use it.
    assert ev["data"]["error_type"] == "unknown"


def test_stderr_context_window_is_recoverable_end_to_end():
    """The real incident: enum collapsed to `unknown`, but stderr carries the
    litellm detail. After folding, the classifier recognises context_window."""
    stderr = [
        "litellm.ContextWindowExceededError: inputs tokens + max_new_tokens "
        "must be <= 32769. Given: 75307 inputs tokens and 32000 max_new_tokens",
    ]
    d = _data(_inline_assistant_error_event("unknown", stderr))
    assert "75307" in d["error_message"]
    assert (
        classify_self_serviceable(d["error_type"], d["error_message"])
        == "context_window"
    )


def test_stderr_is_carried_into_the_message():
    d = _data(_inline_assistant_error_event("invalid_request", ["marker-line-xyz"]))
    assert "marker-line-xyz" in d["error_message"]
