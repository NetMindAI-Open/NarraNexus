"""
@file_name: errors.py
@author: Bin Liang
@date: 2026-07-29
@description: The error-classification contract (minimal-set item A5).

Provider exceptions are normalized into a small closed vocabulary that
downstream machinery branches on: fallback skip decisions, the agent
circuit breaker, frontend actionable badges — and, unique to this
framework, reactive compaction (``CONTEXT_OVERFLOW`` is a signal, not a
failure: the loop compacts and retries the step instead of dying).

The first six values mirror ``loop.events.CLI_ERROR_TYPES`` so the
platform's existing consumers keep working unchanged. The two beyond
them are SIGNALS rather than failures — the loop repairs the request and
retries the step (compaction for ``CONTEXT_OVERFLOW``, a continuation
turn for ``PREFILL_REJECTED``) — and ``legacy_error_type`` keeps that
vocabulary from reaching consumers that never learned it.
"""

from __future__ import annotations

from enum import Enum


class ErrorType(Enum):
    """Closed error vocabulary; extension is an explicit contract change."""

    AUTHENTICATION_FAILED = "authentication_failed"
    BILLING_ERROR = "billing_error"
    RATE_LIMIT = "rate_limit"
    INVALID_REQUEST = "invalid_request"
    SERVER_ERROR = "server_error"
    CONTEXT_OVERFLOW = "context_overflow"  # reactive-compaction trigger
    PREFILL_REJECTED = "prefill_rejected"  # continuation-turn retry trigger
    UNKNOWN = "unknown"


# Values safe to surface through the legacy contract verbatim.
LEGACY_SAFE_ERROR_TYPES = frozenset(
    {
        ErrorType.AUTHENTICATION_FAILED.value,
        ErrorType.BILLING_ERROR.value,
        ErrorType.RATE_LIMIT.value,
        ErrorType.INVALID_REQUEST.value,
        ErrorType.SERVER_ERROR.value,
        ErrorType.UNKNOWN.value,
    }
)


class LoopError(Exception):
    """A classified loop failure.

    Attributes:
        error_type: normalized classification.
        retryable: whether ``RetryPolicy`` may retry the current step.
        provider_raw: the original exception (diagnostics only — never
            shown to users verbatim).
    """

    def __init__(
        self,
        error_type: ErrorType,
        message: str,
        *,
        retryable: bool = False,
        provider_raw: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.retryable = retryable
        self.provider_raw = provider_raw

    def legacy_error_type(self) -> str:
        """The value the legacy contract should carry. ``CONTEXT_OVERFLOW``
        maps to ``invalid_request`` (the closest legacy bucket) when it
        escapes uncompacted — consumers never see unknown vocabulary."""
        if self.error_type.value in LEGACY_SAFE_ERROR_TYPES:
            return self.error_type.value
        return ErrorType.INVALID_REQUEST.value

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"LoopError({self.error_type.value!r}, {self.message!r}, "
            f"retryable={self.retryable})"
        )
