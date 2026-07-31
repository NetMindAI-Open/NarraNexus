"""
@file_name: error_classifier.py
@author: Bin Liang
@date: 2026-07-29
@description: Default ErrorClassifier and RetryPolicy implementations
(contract A5).

Classification is a DATA table (exception-name markers + message
markers), never an if-chain: extending coverage means adding rows.
litellm exception classes are matched by NAME so this module never
imports litellm (the lazy-import discipline lives in llm/litellm_client).

``CONTEXT_OVERFLOW`` is deliberately its own class: the loop treats it
as a compaction signal (compact + retry the step), not a failure.
"""

from __future__ import annotations

import asyncio

from xyz_agent_context.agent_framework.nexus_power.contracts.errors import (
    ErrorType,
    LoopError,
)

# (marker, error_type, retryable) — matched against the exception CLASS
# NAME chain (including causes), first hit wins.
_CLASS_NAME_RULES: tuple[tuple[str, ErrorType, bool], ...] = (
    ("ContextWindowExceeded", ErrorType.CONTEXT_OVERFLOW, True),
    ("AuthenticationError", ErrorType.AUTHENTICATION_FAILED, False),
    ("PermissionDenied", ErrorType.AUTHENTICATION_FAILED, False),
    ("BudgetExceeded", ErrorType.BILLING_ERROR, False),
    ("RateLimitError", ErrorType.RATE_LIMIT, True),
    ("Timeout", ErrorType.SERVER_ERROR, True),
    ("ServiceUnavailable", ErrorType.SERVER_ERROR, True),
    ("InternalServerError", ErrorType.SERVER_ERROR, True),
    ("APIConnectionError", ErrorType.SERVER_ERROR, True),
    ("BadRequestError", ErrorType.INVALID_REQUEST, False),
    ("UnsupportedParams", ErrorType.INVALID_REQUEST, False),
    ("NotFoundError", ErrorType.INVALID_REQUEST, False),
)

# Provider context-overflow message markers (industry-collected set; the
# reactive-compaction trigger). Checked before generic message rules.
_OVERFLOW_MESSAGE_MARKERS: tuple[str, ...] = (
    "context window",
    "context length",
    "context_length_exceeded",
    "maximum context",
    "request too large",
    "request_too_large",
    "prompt is too long",
    "input is too long",
    "too many tokens",
    "exceeds the maximum number of tokens",
    # Anthropic's input+output wall, whose wording shares no marker with
    # the rows above: "input length and `max_tokens` exceed context
    # limit: 154321 + 128000 > 200000". The output clamp is what should
    # keep us off it; these are here so a miss compacts and retries
    # instead of dying as an unretryable INVALID_REQUEST.
    "context limit",
    "exceed context",
)

# Assistant-prefill rejection. Real Anthropic accepts a conversation
# that ends with an assistant turn; some backends behind a load-balancing
# gateway do not, and answer 400. Matched BEFORE the class-name table
# because the wrapper is a plain BadRequestError, which would otherwise
# claim it as a dead-end INVALID_REQUEST.
_PREFILL_MESSAGE_MARKERS: tuple[str, ...] = (
    "does not support assistant message prefill",
    "must end with a user message",
)

_MESSAGE_RULES: tuple[tuple[str, ErrorType, bool], ...] = (
    ("invalid api key", ErrorType.AUTHENTICATION_FAILED, False),
    ("incorrect api key", ErrorType.AUTHENTICATION_FAILED, False),
    ("unauthorized", ErrorType.AUTHENTICATION_FAILED, False),
    ("insufficient credit", ErrorType.BILLING_ERROR, False),
    ("insufficient balance", ErrorType.BILLING_ERROR, False),
    ("credit balance is too low", ErrorType.BILLING_ERROR, False),
    ("quota", ErrorType.BILLING_ERROR, False),
    ("rate limit", ErrorType.RATE_LIMIT, True),
    ("overloaded", ErrorType.SERVER_ERROR, True),
    ("timed out", ErrorType.SERVER_ERROR, True),
)


class DefaultErrorClassifier:
    """Rule-table classifier over the exception chain."""

    def classify(self, exc: BaseException) -> LoopError:
        if isinstance(exc, LoopError):
            return exc
        names = " ".join(type(e).__name__ for e in _chain(exc))
        message = " ".join(str(e) for e in _chain(exc)).strip()
        lowered = message.lower()

        for marker in _OVERFLOW_MESSAGE_MARKERS:
            if marker in lowered:
                return _wrap(exc, ErrorType.CONTEXT_OVERFLOW, message, retryable=True)
        for marker in _PREFILL_MESSAGE_MARKERS:
            if marker in lowered:
                # Retryable, because the shape is usually innocent. Probed
                # on the dev gateway 2026-07-31: the exact conversation
                # that drew this 400 in a live turn — ending in a tool
                # result, not an assistant message — replayed clean three
                # times out of three. The upstream load-balances and only
                # SOME backends refuse, so the rejection says far more
                # about which backend answered than about what we sent.
                # It belongs with the flaky-transport errors; the loop's
                # continuation repair still handles the genuine case
                # where the conversation really does end mid-assistant.
                return _wrap(
                    exc, ErrorType.PREFILL_REJECTED, message, retryable=True
                )
        for marker, error_type, retryable in _CLASS_NAME_RULES:
            if marker in names:
                return _wrap(exc, error_type, message, retryable=retryable)
        for marker, error_type, retryable in _MESSAGE_RULES:
            if marker in lowered:
                return _wrap(exc, error_type, message, retryable=retryable)
        return _wrap(exc, ErrorType.UNKNOWN, message, retryable=False)


def _chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        chain.append(current)
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return chain


def _wrap(
    exc: BaseException, error_type: ErrorType, message: str, *, retryable: bool
) -> LoopError:
    return LoopError(
        error_type,
        message or type(exc).__name__,
        retryable=retryable,
        provider_raw=exc,
    )


class NoRetry:
    """v1 default: an error ends the turn (status-quo behaviour; the
    platform-side helper fallback remains the last resort)."""

    async def should_retry(self, error: LoopError, attempt: int) -> bool:
        return False


class StepRetry:
    """P3 seat: retry the current step for retryable errors. NOTE: this
    bounds RETRIES OF A FAILING CALL, not turn length — entirely
    different from the forbidden turn ceilings (iron rule #14).

    Retries WAIT before firing. Without a backoff the three attempts land
    within milliseconds of each other, which for the two errors that
    actually reach here is worse than useless: a RATE_LIMIT answers a
    burst with a bigger burst, and a SERVER_ERROR gets no time to pass.
    The delay is exponential from a small base and capped, so a long turn
    riding out a provider hiccup never stalls for minutes.
    """

    def __init__(
        self,
        max_attempts_per_step: int = 3,
        *,
        base_delay_s: float = 1.0,
        max_delay_s: float = 15.0,
    ) -> None:
        self._max_attempts = max_attempts_per_step
        self._base_delay_s = base_delay_s
        self._max_delay_s = max_delay_s

    def delay_for(self, attempt: int) -> float:
        """Seconds to wait before attempt N (1-based). Pure, so the
        schedule is testable without sleeping through it."""
        return min(self._base_delay_s * (2 ** max(0, attempt - 1)), self._max_delay_s)

    async def should_retry(self, error: LoopError, attempt: int) -> bool:
        if not (error.retryable and attempt < self._max_attempts):
            return False
        await asyncio.sleep(self.delay_for(attempt))
        return True
