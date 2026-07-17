"""
@file_name: llm_failure.py
@author:
@date: 2026-07-07
@description: Shared helpers for classifying and redacting LLM provider
failures.

Both live here (rather than inside any single trigger) because the same
two questions — "is this a credential/auth failure?" and "how do I show
this error to the owner without leaking their key?" — are asked by every
background LLM path: the message bus, the narrative updater, the Step-5
entity/memory hooks. Before this module the logic existed only inside
``message_bus_trigger`` and the other paths silently swallowed 401s
(2026-07 incident: an expired platform OpenAI key degraded long memory for
~2 weeks with zero owner-facing signal). Consolidating here is the single
source of truth those paths now share.

The classifier reads the RAW error string (keyword markers only, never
displayed). The redactor is what gets shown to the owner. Keep the two
separate — classification must see the unmasked text.
"""

from __future__ import annotations

import re
from typing import Optional, Union

# Substrings (lower-cased) that mark an error as a provider/credential
# problem worth calling out explicitly, vs. a generic failure. Deliberately
# coarse — provider SDKs phrase auth failures many ways, and this only
# decides the owner-facing hint text + audit category, never retry/delivery
# behavior.
CREDENTIAL_ERROR_MARKERS: tuple[str, ...] = (
    "api_key",
    "api key",
    "apikey",
    "credential",
    "unauthorized",
    "authentication",
    " 401",
    "(401",
    " 403",
    "(403",
    "invalid_api_key",
    "invalid api key",
    "provider",
)

# Max length of the (already-redacted) error string embedded anywhere an
# owner can read it. Provider error bodies can be arbitrarily long (stack
# traces, full HTTP response bodies); we only need enough to recognise the
# failure, not a full dump.
MAX_REDACTED_ERROR_LEN = 500

# Patterns for masking secret-looking substrings before an error is echoed
# to a place the owner can read. Provider SDKs frequently echo the offending
# credential back in the error body (OpenAI: "Incorrect API key provided:
# sk-..."), so ``str(exception)`` must never be shown verbatim. Coarse
# pattern masking, not a full secret scanner — it covers the common
# ``sk-...`` / ``key=...`` / ``Bearer ...`` shapes.
_SECRET_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{6,}")
_SECRET_KEYVALUE_PATTERN = re.compile(
    r"\b((?:api[_-]?key|apikey|token|secret|password)\s*[:=]\s*)"
    r"([^\s,;\"']{4,})",
    re.IGNORECASE,
)
_SECRET_BEARER_PATTERN = re.compile(
    r"\bBearer\s+[A-Za-z0-9._-]{8,}", re.IGNORECASE
)


def is_credential_error(error: Union[str, BaseException, None]) -> bool:
    """True when ``error`` looks like a provider auth/credential failure.

    Accepts a string or an exception (``str(exc)`` is used). ``None`` /
    empty → False. Substring match only — see ``CREDENTIAL_ERROR_MARKERS``.
    """
    if error is None:
        return False
    text = str(error).lower()
    if not text:
        return False
    return any(marker in text for marker in CREDENTIAL_ERROR_MARKERS)


# --------------------------------------------------------------------------
# Deterministic, user-self-serviceable failures
# --------------------------------------------------------------------------
# These recur every turn with the same config and can only be fixed by the
# USER changing something (bigger-context model / add credits / fix model id).
# Distinct from `is_credential_error` (auth — handled by its own path) and
# from a transient blip (retry fixes it). A helper-LLM fallback reply MUST NOT
# paper over these — see SELF_SERVICEABLE_ERROR_TYPE in runtime_message.py.
#
# Classification reads the RAW error string (markers, never displayed) and the
# error TYPE (exception class name on the raw-exception path, or the SDK enum
# on the inline path — which may already be collapsed to "unknown", hence the
# message-substring fallback). Positively identified so the residual "our-own
# bug / unattributable" bucket stays untouched.
SELF_SERVICEABLE_REASON_CONTEXT_WINDOW = "context_window"
SELF_SERVICEABLE_REASON_INSUFFICIENT_BALANCE = "insufficient_balance"
SELF_SERVICEABLE_REASON_MODEL_NOT_FOUND = "model_not_found"

# Exact error TYPE (exception class name / SDK enum) → reason. Kept exact to
# avoid substring traps; broader detection is done via the markers below.
_SELF_SERVICEABLE_TYPES: dict[str, str] = {
    "ContextWindowExceededError": SELF_SERVICEABLE_REASON_CONTEXT_WINDOW,
    "billing_error": SELF_SERVICEABLE_REASON_INSUFFICIENT_BALANCE,  # SDK enum
}

# Message-substring markers (lower-cased) per reason. Order of the reason list
# below is significant: most specific first, so a message is attributed to the
# single correct reason.
_CONTEXT_WINDOW_MARKERS: tuple[str, ...] = (
    "context window",
    "context length",
    "context_length_exceeded",
    "contextwindowexceeded",
    "maximum context",
    "max_tokens is too large",
    "reduce the length",
    "must be <=",  # litellm: "inputs tokens ... must be <= N"
)
# A marker is either a plain substring, or a tuple of substrings that must
# ALL be present (an AND-group) — used to keep an over-broad phrase from
# false-positiving on unrelated errors. A false positive is costly here: it
# both mislabels the turn fatal AND makes the circuit breaker skip it (see
# agent_circuit_breaker.record_failure), so a real provider fault needing
# breaker protection could be masked. Hence the deliberately narrow phrasing.
Marker = Union[str, tuple[str, ...]]

_INSUFFICIENT_BALANCE_MARKERS: tuple[Marker, ...] = (
    "insufficient balance",
    "insufficient_quota",
    "insufficient funds",
    "insufficient credit",
    "not enough balance",
    "balance not enough",  # NetMind 400 literal (word order differs from above)
    "credit balance is too low",  # Anthropic: "Your credit balance is too low..."
    "exceeded your current quota",
    "payment required",
    "402 payment",  # narrowed from bare "402" (token counts etc. contain 402)
)
_MODEL_NOT_FOUND_MARKERS: tuple[Marker, ...] = (
    "model not found",
    "model_not_found",
    "no such model",
    "unknown model",
    "invalid model",
    # "does not exist" is too broad alone (a file/conversation can "not
    # exist"); require "model" to co-occur — OpenAI's "The model `x` does not
    # exist or you do not have access to it."
    ("model", "does not exist"),
)

# (reason, markers) in priority order — checked top-to-bottom.
_SELF_SERVICEABLE_MARKERS: tuple[tuple[str, tuple[Marker, ...]], ...] = (
    (SELF_SERVICEABLE_REASON_CONTEXT_WINDOW, _CONTEXT_WINDOW_MARKERS),
    (SELF_SERVICEABLE_REASON_INSUFFICIENT_BALANCE, _INSUFFICIENT_BALANCE_MARKERS),
    (SELF_SERVICEABLE_REASON_MODEL_NOT_FOUND, _MODEL_NOT_FOUND_MARKERS),
)


def _marker_hit(marker: Marker, hay: str) -> bool:
    """True if ``marker`` matches ``hay``: a plain substring, or an AND-group
    (tuple) whose every substring is present."""
    if isinstance(marker, tuple):
        return all(part in hay for part in marker)
    return marker in hay


def classify_self_serviceable(
    error_type: Optional[str], error_message: Optional[str]
) -> Optional[str]:
    """Return the self-serviceable reason for a deterministic, user-fixable
    failure, or ``None`` if the error is not one.

    Reads BOTH the error type (exact class-name / enum match) and the message
    text (substring markers), so it fires on the raw-exception path (type =
    ``ContextWindowExceededError``) AND the inline path (type collapsed to
    ``unknown``, signal only in the folded-in stderr message).
    """
    et = (error_type or "").strip()
    if et in _SELF_SERVICEABLE_TYPES:
        return _SELF_SERVICEABLE_TYPES[et]
    hay = f"{et}\n{error_message or ''}".lower()
    if not hay.strip():
        return None
    for reason, markers in _SELF_SERVICEABLE_MARKERS:
        if any(_marker_hit(m, hay) for m in markers):
            return reason
    return None


def redact_secrets(error: Union[str, BaseException, None], max_len: int = MAX_REDACTED_ERROR_LEN) -> str:
    """Mask secret-looking substrings and cap length for owner display.

    Never a security boundary for arbitrary provider formats — good enough
    for the common credential shapes SDKs echo back.
    """
    text = "" if error is None else str(error)
    text = _SECRET_BEARER_PATTERN.sub("Bearer ***", text)
    text = _SECRET_KEY_PATTERN.sub("sk-***", text)
    text = _SECRET_KEYVALUE_PATTERN.sub(lambda m: f"{m.group(1)}***", text)
    if len(text) > max_len:
        text = text[:max_len] + "... [truncated]"
    return text


# Per-reason user-facing guidance for a self-serviceable failure. Lives here
# (the leaf module) so BOTH response_processor (inline error path) and
# step_3_agent_loop (raw-exception path) compose the SAME actionable message
# without a circular import. Copy is guidance only — never force-stop / model-
# judgement (binding rule #15); whether to act is the user's call.
SELF_SERVICEABLE_USER_MESSAGE: dict[str, str] = {
    SELF_SERVICEABLE_REASON_CONTEXT_WINDOW: (
        "This turn could not run: the selected model's context window is too "
        "small for this Agent's context. Switch to a model with a larger "
        "context window in Settings, then send the message again."
    ),
    SELF_SERVICEABLE_REASON_INSUFFICIENT_BALANCE: (
        "This turn could not run: the model provider reports insufficient "
        "balance / quota. Top up or switch the provider for this Agent slot in "
        "Settings → Providers, which shows which account each key belongs to "
        "(so you top up the right one). A top-up can take a few minutes to take "
        "effect, then send the message again."
    ),
    SELF_SERVICEABLE_REASON_MODEL_NOT_FOUND: (
        "This turn could not run: the configured model id was rejected by the "
        "provider (not found / invalid). Pick a valid model for this Agent "
        "slot in Settings, then send the message again."
    ),
}


def self_serviceable_user_message(reason: str, raw_detail: str) -> str:
    """Compose the user-facing actionable message for a self-serviceable
    failure: per-reason guidance plus the redacted provider detail so the
    concrete cause (token counts, model id) is visible, not hidden behind a
    black-box "unknown"."""
    base = SELF_SERVICEABLE_USER_MESSAGE.get(
        reason,
        "This turn could not run due to a configuration issue you can fix in "
        "Settings, then send the message again.",
    )
    detail = redact_secrets(raw_detail).strip()
    return f"{base}\n\nProvider detail: {detail}" if detail else base
