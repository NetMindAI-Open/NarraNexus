"""
@file_name: test_response_processor_fatal_flag.py
@date: 2026-09-09
@description: ResponseProcessor must honour a framework-reported
``fatal`` flag on a ``response.error`` event. The contract: ``fatal``
means "this turn is terminal AND the turn delivered no usable output" --
NOT merely "this turn ended". ``fatal: true`` (or a framework that
never reports the flag at all) maps to severity="fatal"/"recoverable"
as before; an EXPLICIT ``fatal: false`` -- the turn already delivered a
reply before this failure landed -- maps to
severity="recovered_after_reply", distinct from the generic
"recoverable" default used when no framework has classified the error.

B-05/#127: NexusPower's loop closes the turn with EndReason.ERROR
IMMEDIATELY after any TYPE_ERROR it emits (see loop.py's ``_fail`` and
event_adapter.py's TYPE_ERROR translation) -- there is no "absorbed
mid-stream, kept going" shape for this framework the way there is for
claude/codex's inline API errors. Before this fix every such error fell
into the generic "recoverable" bucket, so a genuinely terminal failure
(e.g. B-03's output-budget truncation after the retry is exhausted) left
the run state=completed with an empty reply and no fatal marker for
anything downstream (is_fatal / _has_fatal_error_frame) to notice.
"""
from __future__ import annotations

from narranexus.platform.agent_runtime.execution_state import ExecutionState
from narranexus.platform.agent_runtime.response_processor import ResponseProcessor
from narranexus.platform.schema import (
    AUTH_EXPIRED_ERROR_TYPE,
    SELF_SERVICEABLE_ERROR_TYPE,
    ErrorMessage,
)


def _error_event(
    error_message: str,
    error_type: str,
    *,
    fatal: bool | None = None,
    self_serviceable: bool | None = None,
) -> dict:
    data = {
        "type": "response.error",
        "error_message": error_message,
        "error_type": error_type,
    }
    if fatal is not None:
        data["fatal"] = fatal
    if self_serviceable is not None:
        data["self_serviceable"] = self_serviceable
    return {"type": "raw_response_event", "data": data}


def _process(event: dict) -> ErrorMessage:
    rp = ResponseProcessor()
    state = ExecutionState()
    results = list(rp.process(event, state))
    msgs = [r.message for r in results if isinstance(r.message, ErrorMessage)]
    assert len(msgs) == 1
    return msgs[0]


def test_fatal_flag_promotes_a_generic_error_to_fatal_severity():
    msg = _process(_error_event(
        "model output truncated: thinking exhausted the output budget "
        "(max_tokens=16384)",
        "invalid_request",
        fatal=True,
    ))
    assert msg.severity == "fatal"
    # error_type is passed through unchanged -- the fatal flag only
    # changes severity, not the classification.
    assert msg.error_type == "invalid_request"
    assert "output budget" in msg.error_message


def test_missing_fatal_flag_keeps_the_historical_recoverable_default():
    """Negative case: claude/codex frameworks never set this flag, and
    their inline API errors CAN be absorbed mid-stream while the CLI
    keeps going -- the default must stay unchanged for them."""
    msg = _process(_error_event("429 too many requests", "rate_limit_error"))
    assert msg.severity == "recoverable"


def test_fatal_false_is_recovered_after_reply_not_recoverable():
    """An EXPLICIT ``fatal: false`` is a framework actively reporting
    "the turn already delivered a reply before this failure landed" --
    that is severity="recovered_after_reply", distinct from the
    "recoverable" default used when no framework has classified the
    error at all (the missing-flag case above)."""
    msg = _process(_error_event("429 too many requests", "rate_limit_error", fatal=False))
    assert msg.severity == "recovered_after_reply"


def test_fatal_flag_does_not_override_auth_or_self_serviceable_classification():
    """Auth and self-serviceable failures resolve to a dedicated,
    actionable error_type -- ``fatal: true`` must not shadow that more
    specific classification. (``fatal: false`` only changes their
    severity, see the tests below.)"""
    msg = _process(_error_event(
        "Please log out and sign in again.", "unauthorized", fatal=True,
    ))
    assert msg.severity == "fatal"
    assert msg.error_type == AUTH_EXPIRED_ERROR_TYPE


def test_fatal_false_downgrades_an_auth_failure_but_keeps_its_classification():
    """A key revoked mid-turn AFTER the framework already delivered a
    reply: the auth branch keeps its dedicated error_type and copy, but
    an explicit ``fatal: false`` must not let it erase the reply."""
    msg = _process(_error_event(
        "Please log out and sign in again.", "unauthorized", fatal=False,
    ))
    assert msg.severity == "recovered_after_reply"
    assert msg.error_type == AUTH_EXPIRED_ERROR_TYPE
    assert msg.self_serviceable is True


def test_fatal_false_downgrades_a_context_window_failure_but_keeps_its_classification():
    """An uncompactable context overflow landing after the reply: same
    rule for the self-serviceable branch. Absent flag / ``fatal: true``
    stay fatal (covered above and below)."""
    msg = _process(_error_event(
        "This model's maximum context length is 8192 tokens",
        "invalid_request", fatal=False,
    ))
    assert msg.severity == "recovered_after_reply"
    assert msg.error_type == SELF_SERVICEABLE_ERROR_TYPE
    assert msg.action_reason == "context_window"


def test_context_window_failure_without_fatal_false_stays_fatal():
    for fatal in (None, True):
        msg = _process(_error_event(
            "This model's maximum context length is 8192 tokens",
            "invalid_request", fatal=fatal,
        ))
        assert msg.severity == "fatal"
        assert msg.error_type == SELF_SERVICEABLE_ERROR_TYPE


def test_framework_fatal_exits_pass_the_drivers_self_serviceable_through():
    """Both framework-classified exits carry the driver's own
    ``self_serviceable`` verdict, like the "recoverable" exit — and
    never fabricate one when the driver did not classify."""
    msg = _process(_error_event(
        "upstream failure", "api_error", fatal=True, self_serviceable=True,
    ))
    assert msg.severity == "fatal"
    assert msg.self_serviceable is True
    msg = _process(_error_event(
        "upstream failure", "api_error", fatal=False, self_serviceable=True,
    ))
    assert msg.severity == "recovered_after_reply"
    assert msg.self_serviceable is True
    msg = _process(_error_event("upstream failure", "api_error", fatal=False))
    assert msg.self_serviceable is None
    msg = _process(_error_event("upstream failure", "api_error", fatal=True))
    assert msg.self_serviceable is None
