"""
@file_name: test_inline_assistant_error_event.py
@date: 2026-07-14
@description: An inline AssistantMessage.error must carry the real provider
cause in the error event, not just the collapsed enum.

AssistantMessage.error is a 6-value enum; the Claude CLI collapses a litellm
ContextWindowExceededError to `unknown`. _inline_assistant_error_event keeps
error_type = the enum (so the classifier can still key on it) and builds
error_message from two channels, in this order (2026-09-03): the CLI's own
inline text is the message, verbatim — for a subscription 429 that text IS
the reason ("Opus is experiencing high load…", "You've hit your limit ·
resets …") — and the CLI stderr tail is appended after it (litellm's token
counts live there), so classify_self_serviceable can still recover "context
window" / "balance not enough" from the message — the end-to-end fix for the
"black box" P1.
"""
from narranexus_plugins.frameworks_claude_code.sdk import (
    _inline_assistant_error_event,
)
from narranexus.platform.agent_framework.llm.failure import classify_self_serviceable


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


def test_inline_text_carries_the_cause_when_stderr_is_empty():
    """The 2026-07-28 incident: stderr was EMPTY and the reason was in the
    message's own TextBlock.

    A user whose agent was pinned to a zero-balance NetMind account saw
    "Claude API error: unknown". The CLI had answered
    `API Error: 400 {"detail":"balance not enough..."}` inline as assistant
    text — we logged the whole thing and then showed the user the bare enum.
    """
    text = (
        'API Error: 400 {"detail":"balance not enough. Please recharge first. '
        'If you have already recharged, please wait 5 minutes and try again."}'
    )
    d = _data(_inline_assistant_error_event("unknown", [], assistant_text=text))
    assert "balance not enough" in d["error_message"]
    assert d["error_type"] == "unknown"


def test_inline_text_balance_error_classifies_as_insufficient_balance():
    """Not just visible — actionable. The classifier must route it to the
    balance bucket so the user is told to top up or switch the slot's provider,
    rather than shown a generic failure."""
    text = 'API Error: 400 {"detail":"balance not enough. Please recharge first."}'
    d = _data(_inline_assistant_error_event("unknown", None, assistant_text=text))
    assert (
        classify_self_serviceable(d["error_type"], d["error_message"])
        == "insufficient_balance"
    )


def test_text_leads_and_stderr_is_appended():
    """The inline text is the message the user reads, verbatim; stderr (which
    carries litellm's numbers) rides behind it so the classifier still sees
    both."""
    d = _data(
        _inline_assistant_error_event(
            "unknown", ["stderr-marker"], assistant_text="text-marker"
        )
    )
    assert d["error_message"].startswith("text-marker")
    assert "stderr-marker" in d["error_message"]
    assert d["error_message"].index("text-marker") < d["error_message"].index("stderr-marker")


def test_no_detail_anywhere_still_produces_the_plain_enum():
    d = _data(_inline_assistant_error_event("server_error", None, assistant_text=""))
    assert d["error_message"] == "Claude API error: server_error"
