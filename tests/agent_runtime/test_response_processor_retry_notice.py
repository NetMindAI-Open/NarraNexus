"""
@file_name: test_response_processor_retry_notice.py
@date: 2026-09-03
@description: A ``response.retry`` event (the Claude adapter is about to
resume the CLI session after a subscription account's transient error)
must reach the user as a step-panel progress row — never as an error, never
as agent text — so the backoff wait is visible without being alarming.
"""
from __future__ import annotations

from narranexus.contracts.agent_events import (
    DATA_TYPE_RETRY,
    TYPE_RAW_RESPONSE_EVENT,
)
from narranexus.platform.agent_runtime.execution_state import ExecutionState
from narranexus.platform.agent_runtime.response_processor import ResponseProcessor
from narranexus.platform.schema import ErrorMessage, ProgressMessage, ProgressStatus


def _retry_event(attempt: int = 2, max_attempts: int = 3, delay: float = 30.0) -> dict:
    return {
        "type": TYPE_RAW_RESPONSE_EVENT,
        "data": {
            "type": DATA_TYPE_RETRY,
            "error_type": "rate_limit",
            "attempt": attempt,
            "max_attempts": max_attempts,
            "delay_seconds": delay,
        },
    }


def test_retry_notice_becomes_a_completed_progress_row():
    rp = ResponseProcessor()
    state = ExecutionState()
    results = list(rp.process(_retry_event(), state))

    progress = [r.message for r in results if isinstance(r.message, ProgressMessage)]
    assert len(progress) == 1
    row = progress[0]
    # COMPLETED on purpose: the popover reports the last RUNNING step as the
    # current activity, and this row must not stay current after the retry.
    assert row.status == ProgressStatus.COMPLETED
    assert "2/3" in row.description
    assert "30" in row.description
    assert row.details["error_type"] == "rate_limit"
    # Not an error: the swallowed failure must not reach the error surface.
    assert not any(isinstance(r.message, ErrorMessage) for r in results)


def test_retry_notice_does_not_touch_the_agent_text():
    rp = ResponseProcessor()
    state = ExecutionState()
    for r in rp.process(_retry_event(), state):
        state = rp.apply_state_update(state, r)
    assert state.final_output == ""
