"""
@file_name: test_llm_failure.py
@date: 2026-07-07
@description: Unit tests for the shared credential-error classifier and
secret redaction used by every background LLM failure path.
"""

import pytest

from xyz_agent_context.agent_framework.llm_failure import (
    is_credential_error,
    redact_secrets,
)


@pytest.mark.parametrize(
    "error",
    [
        "Incorrect API key provided: sk-proj-abc123XYZfXQA",
        "Error code: 401 - unauthorized",
        "AuthenticationError: invalid_api_key",
        "the provider rejected the credential",
        "HTTP 403 Forbidden",
    ],
)
def test_credential_errors_are_classified(error):
    assert is_credential_error(error) is True


@pytest.mark.parametrize(
    "error",
    [
        "Connection reset by peer",
        "Read timed out after 30s",
        "500 Internal Server Error",
        "rate limit; please retry",
    ],
)
def test_non_credential_errors_are_not_classified(error):
    assert is_credential_error(error) is False


def test_accepts_exception_instances():
    assert is_credential_error(RuntimeError("Incorrect API key provided: sk-...")) is True
    assert is_credential_error(RuntimeError("connection refused")) is False


def test_none_and_empty_are_not_credential():
    assert is_credential_error("") is False
    assert is_credential_error(None) is False


def test_redact_masks_openai_style_key():
    out = redact_secrets("Incorrect API key provided: sk-proj-abc123XYZfQA9")
    assert "sk-proj-abc123XYZfQA9" not in out
    assert "sk-***" in out


def test_redact_masks_bearer_and_keyvalue():
    out = redact_secrets("call failed api_key=supersecretvalue Bearer abcdef123456")
    assert "supersecretvalue" not in out
    assert "abcdef123456" not in out


def test_redact_truncates_long_bodies():
    out = redact_secrets("x" * 5000, max_len=500)
    assert len(out) <= 500 + len("... [truncated]")
    assert out.endswith("... [truncated]")
