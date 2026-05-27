"""
@file_name: test_error_message_severity.py
@author: Bin Liang
@date: 2026-05-25
@description: Pins the ErrorMessage.severity literal contract.

Recovery semantics added 2026-05-25 for the fallback-context redesign:
  - "recovered": a fatal-class error happened, but helper_llm produced
    a user-facing reply that masks the failure operationally. Frontend
    should render the recovered reply normally and show the error as a
    warning badge.
  - "recovered_after_reply": the agent successfully called
    send_message_to_user_directly first, *then* a fatal happened.
    No fallback runs (we already spoke); the badge surfaces the
    truncated execution so the user knows the turn didn't finish all
    planned work.

Default remains "fatal" — historical sites that build ErrorMessage
without specifying severity keep their old behaviour.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from xyz_agent_context.schema import ErrorMessage


def test_default_severity_is_fatal():
    msg = ErrorMessage(error_message="boom")
    assert msg.severity == "fatal"


def test_fatal_severity_accepted():
    msg = ErrorMessage(error_message="boom", severity="fatal")
    assert msg.severity == "fatal"


def test_recoverable_severity_accepted():
    msg = ErrorMessage(error_message="rate limit", severity="recoverable")
    assert msg.severity == "recoverable"


def test_recovered_severity_accepted():
    """Fatal-class error followed by successful helper_llm fallback."""
    msg = ErrorMessage(
        error_message="agent loop timed out", severity="recovered"
    )
    assert msg.severity == "recovered"


def test_recovered_after_reply_severity_accepted():
    """Fatal happened AFTER the agent already sent a real reply."""
    msg = ErrorMessage(
        error_message="follow-up tool failed", severity="recovered_after_reply"
    )
    assert msg.severity == "recovered_after_reply"


def test_unknown_severity_rejected():
    with pytest.raises(ValidationError):
        ErrorMessage(error_message="x", severity="garbage")
