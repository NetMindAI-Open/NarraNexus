"""
@file_name: test_llm_failure.py
@date: 2026-07-07
@description: Unit tests for the shared credential-error classifier and
secret redaction used by every background LLM failure path.
"""

import pytest

from narranexus.platform.agent_framework.llm.failure import (
    is_auth_like_error,
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
        # The word "provider" on its own is not a credential problem. Until
        # 2026-09-09 a bare "provider" substring marker swept every one of
        # these into the credential bucket — the owner was then told to check
        # an API key that was fine (upstream NetMindAI-Open/NarraNexus#106:
        # a worker crashing on a stale module path got "check your Provider
        # settings").
        "provider temporarily unavailable",
        "no provider configured for slot default",
        "No module named 'narranexus.agent_framework.provider_resolver'",
        "provider returned 500 Internal Server Error",
        # Digit markers must be whole numbers, not substrings of a count.
        "generated 403 tokens before the stream ended",
        "request id 14012 failed: connection reset",
    ],
)
def test_non_credential_errors_are_not_classified(error):
    assert is_credential_error(error) is False


@pytest.mark.parametrize(
    "error",
    [
        "Error code: 401 - {'error': 'bad'}",
        "HTTP 403 Forbidden",
        "403 Forbidden",
        "(401) unauthorized",
        "Invalid API token",
        "invalid_token",
        # Review C4: real provider strings where `_` or a letter abuts the
        # credential word. All were substring hits before 2026-09-09 and must
        # stay hits — background_llm_alerts writes its owner notice on this.
        "AuthenticationError",
        "openai.AuthenticationError",
        "litellm.AuthenticationError: request failed",
        "AuthenticationException: token rejected",
        "authentication_failed",
        "authentication_error",
        "api_key_invalid",
        "APIKeyError",
        "missing x_api_key header",
        "no_api_key configured for slot",
        "AuthenticationRequired",
        "the api_keys table has no row",
    ],
)
def test_status_codes_and_token_phrases_are_credential(error):
    assert is_credential_error(error) is True


class AuthenticationError(Exception):
    """A provider SDK's auth exception with an uninformative body."""


class PermissionDeniedError(Exception):
    pass


def test_exception_type_name_is_a_credential_signal():
    assert is_credential_error(AuthenticationError("request failed")) is True
    assert is_credential_error(PermissionDeniedError("nope")) is True
    assert is_credential_error(RuntimeError("request failed")) is False


@pytest.mark.parametrize("error", [
    "Error code: 503 - {'detail': 'API key channel binding is temporarily unavailable'}",
    "API key channel binding is temporarily unavailable",
    "HTTP 502: authentication service unavailable",
    "Authentication service timed out",
])
def test_provider_outage_is_not_a_credential_refusal(error):
    assert is_credential_error(error) is False
    assert is_auth_like_error(error) is False


def test_explicit_auth_status_and_exception_override_outage_wording():
    assert is_credential_error(AuthenticationError("authentication temporarily unavailable"))
    assert is_credential_error("HTTP 401: invalid API key; retry after a timeout")


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


def test_forbidden_is_auth_like_but_not_a_credential_error():
    """#389 I3: "forbidden" classifies a failed turn as AUTH (loose predicate)
    but must not drive control flow — the Claude CLI resume path skips its
    cold retry on `is_credential_error`, and a sandbox policy refusal is not
    a dead credential."""
    assert is_auth_like_error("write to /etc is forbidden by policy") is True
    assert is_credential_error("write to /etc is forbidden by policy") is False
    # A real 403 still classifies strictly, via the status code.
    assert is_credential_error("HTTP 403 Forbidden") is True


@pytest.mark.parametrize("error", ["HTTP403 from upstream", "x403y", "id_401_abc", "req401"])
def test_status_codes_glued_to_identifiers_are_not_credential(error):
    assert is_credential_error(error) is False
    assert is_auth_like_error(error) is False


class AuthenticationError(Exception):
    """Named like the provider SDKs' auth exception (matched by class name)."""


def test_an_auth_timeout_is_an_outage_not_a_rejected_key():
    """An auth service that timed out has not rejected the key — a retry can
    help, so the strict predicate must say False. A real rejection is still
    caught first, by its status code or by the SDK's auth exception class,
    whatever else its text says."""
    assert is_credential_error("Authentication request timed out") is False
    assert is_credential_error("401 Unauthorized: authentication request timed out") is True
    assert is_credential_error(AuthenticationError("upstream timed out")) is True
