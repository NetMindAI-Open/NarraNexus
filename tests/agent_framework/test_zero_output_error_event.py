"""
@file_name: test_zero_output_error_event.py
@author: Bin Liang
@date: 2026-07-03
@description: The 0-message Claude-CLI run must emit a classifiable error.

When the Claude Code CLI yields zero messages (expired OAuth / not logged in /
crash / quota) the run used to end silently, so the pipeline treated it as
"agent chose not to reply" and the helper-LLM fabricated a hollow fallback.
_zero_output_error_event now emits a response.error carrying the raw CLI
stderr, and response_processor._is_auth_failure classifies it: an auth/login
stderr → fatal AUTH_EXPIRED (re-login, no fabrication); anything else stays a
recoverable "no output" error. The base sentence must be auth-phrase-free so an
empty stderr is never misclassified as an auth failure.
"""
from xyz_agent_context.agent_framework.xyz_claude_agent_sdk import (
    _zero_output_error_event,
)
from xyz_agent_context.agent_runtime.response_processor import _is_auth_failure


def _data(ev):
    return ev["data"]


def test_event_has_response_error_shape():
    ev = _zero_output_error_event([])
    assert ev["type"] == "raw_response_event"
    assert ev["data"]["type"] == "response.error"
    assert ev["data"]["error_type"] == "no_output"


def test_empty_stderr_is_not_misclassified_as_auth():
    d = _data(_zero_output_error_event([]))
    assert _is_auth_failure(d["error_type"], d["error_message"]) is False


def test_login_stderr_classifies_as_auth_failure():
    d = _data(_zero_output_error_event(["Error: Not logged in. Run `claude login` to authenticate."]))
    assert _is_auth_failure(d["error_type"], d["error_message"]) is True


def test_expired_token_stderr_classifies_as_auth_failure():
    d = _data(_zero_output_error_event(["OAuth refresh token could not be refreshed (401)"]))
    assert _is_auth_failure(d["error_type"], d["error_message"]) is True


def test_generic_crash_stderr_stays_recoverable():
    d = _data(_zero_output_error_event(["Segmentation fault (core dumped)"]))
    assert _is_auth_failure(d["error_type"], d["error_message"]) is False


def test_stderr_is_carried_into_the_message():
    d = _data(_zero_output_error_event(["a-very-specific-stderr-line"]))
    assert "a-very-specific-stderr-line" in d["error_message"]
