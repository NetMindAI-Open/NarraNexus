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

from xyz_agent_context.agent_framework.llm.failure import (
    classify_self_serviceable,
    OUT_OF_CREDIT_REASONS,
    SELF_SERVICEABLE_REASON_CONTEXT_WINDOW,
    SELF_SERVICEABLE_REASON_FREE_TIER_EXHAUSTED,
    SELF_SERVICEABLE_REASON_INSUFFICIENT_BALANCE,
    SELF_SERVICEABLE_REASON_INVALID_CREDENTIALS,
    SELF_SERVICEABLE_REASON_MODEL_NOT_FOUND,
    SELF_SERVICEABLE_USER_MESSAGE,
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
        # credential REJECTED by the provider (403 family). Distinct from the
        # auth-expired path, which covers a dead OAuth/CLI login and tells the
        # user to re-login: here they hold an API key the provider refuses, and
        # the fix is to re-paste/rotate it. 2026-07-29 report (Jiaxi): a BYOK
        # NetMind key returned `403 Invalid api token`, matched NOTHING, and the
        # turn fell through to a fabricated helper reply — the user saw the
        # agent promise work it never did.
        (
            "invalid_request",
            'Claude API error: invalid_request\n\nProvider response:\n'
            'API Error: 403 {"error":{"message":"Invalid api token"}}',
            SELF_SERVICEABLE_REASON_INVALID_CREDENTIALS,
        ),
        ("unknown", "API Error: 403 Invalid api token", SELF_SERVICEABLE_REASON_INVALID_CREDENTIALS),
        ("unknown", "Error code: 403 - Forbidden", SELF_SERVICEABLE_REASON_INVALID_CREDENTIALS),
        ("unknown", "403 Forbidden", SELF_SERVICEABLE_REASON_INVALID_CREDENTIALS),
        ("unknown", "No auth credentials found", SELF_SERVICEABLE_REASON_INVALID_CREDENTIALS),
        ("unknown", "invalid_api_token", SELF_SERVICEABLE_REASON_INVALID_CREDENTIALS),
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
        # - digits "403" inside a token count must NOT read as HTTP 403. The
        #   credential markers require credential/permission vocabulary next to
        #   the status for exactly this reason: pairing "403" with "token" alone
        #   matched this line during development.
        ("unknown", "generated 403 tokens before the stream ended"),
        ("unknown", "the run produced 4030 tokens across 12 turns"),
        # - a 401 stays with the dedicated auth path (re-login copy), it must
        #   not be re-routed to the credential-rejected reason
        ("unauthorized", "Error code: 401 - unauthorized"),
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


# --------------------------------------------------------------------------
# Free-tier exhaustion vs a BYOK wallet running dry (2026-07-30)
# --------------------------------------------------------------------------
# These are the SAME user-visible condition ("no money") with OPPOSITE remedies,
# so they must not share a reason:
#
#   * free tier  — a $10 per-user budget enforced by OUR LiteLLM gateway. The
#     user cannot top it up (that route is staff-only) and cannot re-paste its
#     key (the gateway showed it once, to the server). Their real way forward is
#     to subscribe or bring their own provider.
#   * BYOK       — the user's own account is out of credit. "Top up or switch"
#     is exactly right, and this copy must not drift while we add the other.
#
# The signal is the MARKER, not the card: only our gateway enforces a per-user
# budget, and it says so in the body. Attribution therefore reports what the
# gateway actually said rather than inferring from configuration — which also
# means an upstream outage (NetMind's "balance not enough") stays generic
# instead of telling the user to go buy something over OUR failure.


@pytest.mark.parametrize(
    "error_message",
    [
        # LiteLLM's per-user budget refusals, verbatim shapes.
        "litellm.BudgetExceededError: Budget has been exceeded! Current cost: 10.0",
        "ExceededBudget: crossed spend within budget",
        "exceeded budget for key",
    ],
)
def test_gateway_budget_is_free_tier_exhausted(error_message):
    assert (
        classify_self_serviceable("unknown", error_message)
        == SELF_SERVICEABLE_REASON_FREE_TIER_EXHAUSTED
    )


@pytest.mark.parametrize(
    "error_type,error_message",
    [
        # NetMind upstream — this is ALSO what a dry shared free-tier upstream
        # looks like, and it must stay generic: blaming the user's spending for
        # our own outage is the one wrong answer here.
        ("unknown", '{"message":"balance not enough"}'),
        ("unknown", "Your credit balance is too low to access the Anthropic API"),
        ("unknown", "You exceeded your current quota, please check your plan"),
        ("billing_error", "whatever"),
        ("unknown", "402 payment required"),
    ],
)
def test_byok_out_of_credit_stays_insufficient_balance(error_type, error_message):
    """The load-bearing regression guard for this change."""
    assert (
        classify_self_serviceable(error_type, error_message)
        == SELF_SERVICEABLE_REASON_INSUFFICIENT_BALANCE
    )


@pytest.mark.parametrize(
    "error_message",
    [
        "Budget has been exceeded! Current cost: 10.0, Max budget: 10.0",
        "ExceededBudget: crossed spend within budget",
    ],
)
def test_a_typed_billing_error_still_yields_to_the_free_tier_marker(error_message):
    """A type-table hit names the CATEGORY; the message still picks WHICH.

    ``billing_error`` is an SDK enum meaning only "no money". Returning on it
    without reading the body would hand a spent free-tier wallet the BYOK
    guidance — top up, switch the provider — which is the exact pair this whole
    change exists to stop showing, since neither is possible for that card.
    Only reachable on the raw-exception path (the inline path collapses
    error_type to ``unknown``), which is why it went unnoticed.
    """
    assert (
        classify_self_serviceable("billing_error", error_message)
        == SELF_SERVICEABLE_REASON_FREE_TIER_EXHAUSTED
    )


def test_a_typed_hit_outside_out_of_credit_is_not_second_guessed():
    """The refinement is scoped to the no-money reasons. A context-window error
    whose body happens to mention a budget must stay a context-window error."""
    assert (
        classify_self_serviceable(
            "ContextWindowExceededError", "budget has been exceeded"
        )
        == SELF_SERVICEABLE_REASON_CONTEXT_WINDOW
    )


def test_byok_balance_copy_still_offers_top_up_and_switch():
    """BYOK guidance is correct as written — a top-up IS possible on the user's
    own account. Adding the free-tier branch must not bleed into it."""
    copy = SELF_SERVICEABLE_USER_MESSAGE[SELF_SERVICEABLE_REASON_INSUFFICIENT_BALANCE]
    assert "Top up" in copy and "switch the provider" in copy
    assert "free" not in copy.lower()  # no free-tier vocabulary leaking in


def test_free_tier_copy_never_tells_the_user_to_top_up():
    """Topping up this wallet needs `role=staff` (/api/admin/quota/topup), and
    its key was never in the user's hands — so both of the BYOK remedies are
    impossible here. The copy must offer what they CAN do instead."""
    copy = SELF_SERVICEABLE_USER_MESSAGE[SELF_SERVICEABLE_REASON_FREE_TIER_EXHAUSTED]
    assert "top up" not in copy.lower()
    assert "re-paste" not in copy.lower()
    assert "subscri" in copy.lower()  # the actually-available path


# --------------------------------------------------------------------------
# OUT_OF_CREDIT_REASONS — the set two incident guards depend on
# --------------------------------------------------------------------------
# Splitting free-tier exhaustion off `insufficient_balance` broke both of these
# on first attempt, because each compared against the single old constant:
#
#   * circuit_breaker._is_out_of_credit → without QUOTA the breaker never pauses,
#     so an exhausted user retries forever;
#   * job_trigger._EDGE_ONLY_RESUME_REASONS → a reason missing from it is handed
#     back to the blind time-based backstop, which re-arms paused jobs every
#     cycle — the 390-retry storm.
#
# Both were caught by existing tests only because those tests happened to use the
# gateway-budget message. These assert the RELATIONSHIP, so the next reason added
# cannot quietly fall out of either guard.


def test_out_of_credit_reasons_holds_every_no_money_reason():
    assert SELF_SERVICEABLE_REASON_INSUFFICIENT_BALANCE in OUT_OF_CREDIT_REASONS
    assert SELF_SERVICEABLE_REASON_FREE_TIER_EXHAUSTED in OUT_OF_CREDIT_REASONS
    # Reasons that are NOT about money must stay out — they have different
    # remedies and different resume semantics.
    assert SELF_SERVICEABLE_REASON_CONTEXT_WINDOW not in OUT_OF_CREDIT_REASONS
    assert SELF_SERVICEABLE_REASON_MODEL_NOT_FOUND not in OUT_OF_CREDIT_REASONS
    assert SELF_SERVICEABLE_REASON_INVALID_CREDENTIALS not in OUT_OF_CREDIT_REASONS


def test_every_out_of_credit_reason_pauses_the_circuit_breaker():
    from xyz_agent_context.agent_framework.loop.circuit_breaker import (
        ErrorCategory,
        classify_agent_error,
    )

    # One representative raw message per reason, classified end-to-end.
    samples = {
        SELF_SERVICEABLE_REASON_INSUFFICIENT_BALANCE: '{"message":"balance not enough"}',
        "free_tier_exhausted": "litellm.BudgetExceededError: Budget has been exceeded!",
    }
    assert set(samples) == set(OUT_OF_CREDIT_REASONS), (
        "a new out-of-credit reason needs a sample here — otherwise this guard "
        "silently stops covering it"
    )
    for reason, raw in samples.items():
        assert classify_self_serviceable("unknown", raw) == reason
        assert classify_agent_error("unknown", raw) == ErrorCategory.QUOTA


def test_every_out_of_credit_reason_resumes_only_on_an_edge():
    """A balance top-up leaves config unchanged, so the static readiness check
    cannot observe it. Any out-of-credit reason must therefore be edge-only."""
    from xyz_agent_context.module.job_module.job_trigger import (
        _EDGE_ONLY_RESUME_REASONS,
    )

    assert OUT_OF_CREDIT_REASONS <= _EDGE_ONLY_RESUME_REASONS
