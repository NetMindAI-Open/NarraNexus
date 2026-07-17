"""
@file_name: test_self_serviceable.py
@date: 2026-07-14
@description: Unit tests for classify_self_serviceable — the deterministic,
user-self-serviceable failure classifier. These are errors that recur every
turn with the same config (context window too small, no credits, bad model
id) and so must NOT be masked behind a helper-LLM fallback reply.

Root case being guarded: a NetMind user on a 32k model whose turn fails with
`litellm.ContextWindowExceededError: inputs 75307 > 32769`, which the Claude
CLI collapses to the enum `unknown`. The classifier must recognise it from
EITHER the exception class name (raw-exception path) OR the message substring
(inline path, once SDK stderr is folded into error_message).
"""

import pytest

from xyz_agent_context.agent_framework.llm_failure import (
    classify_self_serviceable,
    SELF_SERVICEABLE_REASON_CONTEXT_WINDOW,
    SELF_SERVICEABLE_REASON_INSUFFICIENT_BALANCE,
    SELF_SERVICEABLE_REASON_MODEL_NOT_FOUND,
)


@pytest.mark.parametrize(
    "error_type,error_message,expected",
    [
        # raw-exception path: class name is preserved
        ("ContextWindowExceededError", "whatever", SELF_SERVICEABLE_REASON_CONTEXT_WINDOW),
        # inline path: type collapsed to `unknown`, signal only in the message
        (
            "unknown",
            "litellm.ContextWindowExceededError: inputs tokens + max_new_tokens "
            "must be <= 32769. Given: 75307 inputs tokens and 32000 max_new_tokens",
            SELF_SERVICEABLE_REASON_CONTEXT_WINDOW,
        ),
        ("unknown", "This model's maximum context length is 8192 tokens", SELF_SERVICEABLE_REASON_CONTEXT_WINDOW),
        ("invalid_request", "context_length_exceeded", SELF_SERVICEABLE_REASON_CONTEXT_WINDOW),
        # insufficient balance / credits (across providers + the exact upstream
        # incident literals: NetMind "balance not enough" (400) / "Insufficient
        # Balance" (402), Anthropic "credit balance is too low")
        ("billing_error", "", SELF_SERVICEABLE_REASON_INSUFFICIENT_BALANCE),
        ("unknown", "You have insufficient balance to use this model", SELF_SERVICEABLE_REASON_INSUFFICIENT_BALANCE),
        ("unknown", "402 Payment Required", SELF_SERVICEABLE_REASON_INSUFFICIENT_BALANCE),
        ("unknown", "Error code: 402 - Insufficient Balance", SELF_SERVICEABLE_REASON_INSUFFICIENT_BALANCE),
        ("unknown", "balance not enough", SELF_SERVICEABLE_REASON_INSUFFICIENT_BALANCE),
        ("unknown", "Your credit balance is too low to access the Claude API", SELF_SERVICEABLE_REASON_INSUFFICIENT_BALANCE),
        # bad / missing model id
        ("unknown", "The model `gpt-nope` does not exist", SELF_SERVICEABLE_REASON_MODEL_NOT_FOUND),
        ("unknown", "model_not_found", SELF_SERVICEABLE_REASON_MODEL_NOT_FOUND),
    ],
)
def test_self_serviceable_is_classified(error_type, error_message, expected):
    assert classify_self_serviceable(error_type, error_message) == expected


@pytest.mark.parametrize(
    "error_type,error_message",
    [
        # transient — retry fixes it, must NOT be treated as self-serviceable
        ("RateLimitError", "429 too many requests, please retry"),
        ("APITimeoutError", "Read timed out after 30s"),
        ("unknown", "503 Service Unavailable, server is overloaded"),
        ("ConnectionError", "Connection reset by peer"),
        # auth — handled by the dedicated auth path, not here
        ("unauthorized", "Error code: 401 - unauthorized"),
        ("unknown", "Incorrect API key provided"),
        # generic / our-own bug — the residual BUSINESS bucket, untouched
        ("Exception", "some unexpected internal error"),
        ("unknown", "Claude API error: unknown"),
        # narrowed markers must NOT false-positive (a false hit here would also
        # make the circuit breaker skip a real fault):
        # - "does not exist" without "model" (a file / conversation)
        ("NotFoundError", "The conversation does not exist"),
        ("unknown", "file does not exist on disk"),
        # - a bare "402" inside token counts, not a payment error
        ("unknown", "sequence length 402 exceeds nothing in particular"),
    ],
)
def test_non_self_serviceable_is_not_classified(error_type, error_message):
    assert classify_self_serviceable(error_type, error_message) is None


def test_none_and_empty_return_none():
    assert classify_self_serviceable(None, None) is None
    assert classify_self_serviceable("", "") is None


def test_context_window_wins_over_other_markers():
    # A message that mentions both context and (incidentally) a number must
    # still resolve to the most specific, correct reason.
    assert (
        classify_self_serviceable("unknown", "maximum context length exceeded")
        == SELF_SERVICEABLE_REASON_CONTEXT_WINDOW
    )
