"""
@file_name: failure.py
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
SELF_SERVICEABLE_REASON_FREE_TIER_EXHAUSTED = "free_tier_exhausted"
SELF_SERVICEABLE_REASON_MODEL_NOT_FOUND = "model_not_found"
SELF_SERVICEABLE_REASON_INVALID_CREDENTIALS = "invalid_credentials"

# Every reason that means "this credential has no money left". Consumers asking
# that question MUST test membership, not equality against one member.
#
# Learned the hard way on 2026-07-30: splitting free-tier exhaustion out of
# `insufficient_balance` silently broke two incident guards that compared against
# the single constant — the circuit breaker stopped categorising it as QUOTA (so
# an exhausted user retried forever) and job_trigger stopped treating it as
# edge-only-resume (so the time backstop re-armed paused jobs every cycle, which
# is precisely the 390-retry storm). Both were caught by existing tests; this set
# is what keeps the NEXT such reason from re-breaking them.
OUT_OF_CREDIT_REASONS: frozenset[str] = frozenset({
    SELF_SERVICEABLE_REASON_INSUFFICIENT_BALANCE,
    SELF_SERVICEABLE_REASON_FREE_TIER_EXHAUSTED,
})

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
# OUR OWN LiteLLM gateway refusing a per-user budget — i.e. the free-tier wallet
# is spent. Split out of the generic balance markers above (2026-07-30) because
# the two conditions look identical to the user and have OPPOSITE remedies: this
# one cannot be topped up (that route is staff-only) and its key was never in the
# user's hands, so the generic "top up or re-paste" guidance is impossible to act
# on. See SELF_SERVICEABLE_USER_MESSAGE.
#
# The marker is the whole signal, deliberately: only our gateway enforces a
# per-user budget, and it says so in the body. Reading it here — rather than
# inferring "is this the free-tier card" from configuration — is what keeps an
# UPSTREAM outage honest: a dry shared upstream answers with NetMind's "balance
# not enough", stays `insufficient_balance`, and therefore never tells the user
# to go buy something over a failure that is ours.
#
# Known false positive, accepted: a user whose OWN provider is itself a LiteLLM
# proxy with per-key budgets (custom_openai / custom_anthropic take any base_url)
# produces the same body. The cost is misdirected advice, not a broken flow.
# Eliminating it needs the per-slot card source, which `get_provider_source()`
# cannot give today (providers/resolver.py hardcodes "user" for every user card).
_FREE_TIER_BUDGET_MARKERS: tuple[Marker, ...] = (
    "budget has been exceeded",
    "exceeded budget",
    "crossed spend within budget",
    "exceededbudget",
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
# The provider REFUSES the credential it was handed (HTTP 403 family) — as
# opposed to a dead OAuth/CLI login, which ``response_processor._is_auth_failure``
# already routes to ``auth_expired`` with re-login guidance. The distinction is
# the remedy, which is why this is its own reason: a user holding a rejected API
# key must be told to re-paste / rotate the key, not to run `claude setup-token`.
#
# 2026-07-29 (reported by Jiaxi): a BYOK NetMind key returned
# ``403 {"error":{"message":"Invalid api token"}}``. It matched NOTHING — the
# auth phrases carry "401" and "invalid api key", while this body says 403 and
# "api token" — so the turn stayed "recoverable" and the helper-LLM fallback
# wrote a plausible reply over work that never happened.
#
# Markers are word/delimiter anchored on purpose: a bare "403" also appears
# inside token counts ("403 tokens", "<= 4030"), and a false positive here both
# mislabels the turn fatal AND makes the circuit breaker skip a real fault.
# Same narrowing discipline as "402 payment" above.
_INVALID_CREDENTIALS_MARKERS: tuple[Marker, ...] = (
    "invalid api token",  # NetMind's 403 literal (the incident)
    "invalid_api_token",
    "invalid bearer token",
    "no auth credentials",  # OpenRouter's phrasing
    # Generic 403 shapes: the status code must co-occur with credential/permission
    # vocabulary. Pairing "403" with a bare "token" is NOT enough — "generated 403
    # tokens before the stream ended" satisfies it (caught in test).
    ("403", "forbidden"),
    ("403", "invalid token"),
    ("403", "invalid api"),
    ("403", "credential"),
    ("403", "permission denied"),
)

# (reason, markers) in priority order — checked top-to-bottom.
# ``invalid_credentials`` sits LAST so every message that classified before this
# reason existed keeps its previous reason (e.g. a 403 body that also reports a
# spent balance stays ``insufficient_balance``, whose remedy is the useful one).
_SELF_SERVICEABLE_MARKERS: tuple[tuple[str, tuple[Marker, ...]], ...] = (
    (SELF_SERVICEABLE_REASON_CONTEXT_WINDOW, _CONTEXT_WINDOW_MARKERS),
    # BEFORE the generic balance markers: "budget has been exceeded" would
    # otherwise be swallowed by them and lose the only remedy the user can act on.
    (SELF_SERVICEABLE_REASON_FREE_TIER_EXHAUSTED, _FREE_TIER_BUDGET_MARKERS),
    (SELF_SERVICEABLE_REASON_INSUFFICIENT_BALANCE, _INSUFFICIENT_BALANCE_MARKERS),
    (SELF_SERVICEABLE_REASON_MODEL_NOT_FOUND, _MODEL_NOT_FOUND_MARKERS),
    (SELF_SERVICEABLE_REASON_INVALID_CREDENTIALS, _INVALID_CREDENTIALS_MARKERS),
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
    hay = f"{et}\n{error_message or ''}".lower()
    typed = _SELF_SERVICEABLE_TYPES.get(et)
    if typed is not None:
        # A type-table hit names the CATEGORY; for the out-of-credit ones the
        # message still decides WHICH credit ran out. ``billing_error`` is an SDK
        # enum meaning no more than "no money", so returning on it blind would
        # hand a spent free-tier wallet the BYOK guidance (top up / switch the
        # provider) — the very pair that is impossible for that card and the
        # reason ``free_tier_exhausted`` exists.
        #
        # Scoped to OUT_OF_CREDIT_REASONS on purpose: a context-window error is
        # not made a budget error by the word "budget" appearing in its body.
        if typed in OUT_OF_CREDIT_REASONS and any(
            _marker_hit(m, hay) for m in _FREE_TIER_BUDGET_MARKERS
        ):
            return SELF_SERVICEABLE_REASON_FREE_TIER_EXHAUSTED
        return typed
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
    # Deliberately provider-agnostic: this fires for DeepSeek 402s, OpenAI
    # insufficient_quota, Anthropic credit-balance AND a spent free-tier wallet
    # alike, so it must not name one vendor's remedy. NetMind-specific guidance
    # (subscribe to a plan) lives in the resolver's no-provider error instead,
    # i.e. cloud + NetMind by construction, so it is always relevant there.
    SELF_SERVICEABLE_REASON_INSUFFICIENT_BALANCE: (
        "This turn could not run: the model provider reports insufficient "
        "balance / quota. Top up or switch the provider for this Agent slot in "
        "Settings → Providers, which shows which account each key belongs to "
        "(so you top up the right one). A top-up can take a few minutes to take "
        "effect, then send the message again."
    ),
    # Neither of the BYOK remedies exists here, so neither is offered: the wallet
    # is topped up only by staff (/api/admin/quota/topup), and its key was shown
    # once to the server and never to the user. What IS available is the funnel
    # this copy restores — the pre-2026-07-28 flow said the same thing from an
    # HTTP 402 banner, and that trigger disappeared when the free tier became an
    # ordinary provider card.
    SELF_SERVICEABLE_REASON_FREE_TIER_EXHAUSTED: (
        # Keeps the WHERE that the chat copy drops: this string also reaches
        # surfaces with no buttons to click — a paused job's failure reason, the
        # background-LLM alert — so it has to stand on its own.
        "This turn could not run: your free platform credit is used up — "
        "upgrade to Nexus Pro in Settings → Account & Subscription, or add your "
        "own provider key and switch this Agent's slot to it in "
        "Settings → Providers."
    ),
    SELF_SERVICEABLE_REASON_MODEL_NOT_FOUND: (
        "This turn could not run: the configured model id was rejected by the "
        "provider (not found / invalid). Pick a valid model for this Agent "
        "slot in Settings, then send the message again."
    ),
    # Deliberately about the KEY, never about re-login: the auth_expired path
    # owns dead OAuth/CLI sessions and says `claude setup-token` there. Getting
    # these two mixed up sends a BYOK user chasing a login they never used.
    SELF_SERVICEABLE_REASON_INVALID_CREDENTIALS: (
        "This turn could not run: the model provider rejected the credential "
        "for this Agent slot (403 / invalid token) — a key that was revoked, "
        "rotated upstream, or pasted incompletely does this. Check or re-paste "
        "the key in Settings → Providers, then send the message again."
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


# --------------------------------------------------------------------------
# Executor infrastructure failures (PLATFORM-side)
# --------------------------------------------------------------------------
# Distinct from the self-serviceable class above: the user CANNOT fix these by
# changing their config — the platform's per-user execution container ran out
# of memory (subprocess SIGKILL/SIGABRT) or became unreachable (container not
# up / broker down / :8020 connection dropped). Like the self-serviceable
# class, a helper-LLM fallback reply MUST NOT paper over them — that would hide
# an OOM / dropped container behind a fabricated answer (the exact "black box"
# failure mode). Surfaced to the owner as ``infra_transient`` (retry / split
# the task), NEVER as a force-stop or model judgement (binding rules #14/#15).
#
# Two recognition channels, on purpose:
#   - OOM: only signal available is the child-process returncode folded into
#     the error string ("exit code -9" = SIGKILL/OOM, "exit code -6" = SIGABRT)
#     — substring match. Positive exit codes (a tool the agent ran failed) are
#     NOT infra and must not match.
#   - Unreachable: the executor boundary raises the typed
#     ``ExecutorUnreachableError`` (see agent_framework/loop/executor_errors.py) —
#     matched by exception class NAME, not fragile text matching. This keeps a
#     USER's LLM-provider connection blip (which arrives as a response.error
#     frame / transient, handled elsewhere) from being misread as executor
#     infra.
EXECUTOR_INFRA_REASON_OOM = "executor_oom"
EXECUTOR_INFRA_REASON_UNREACHABLE = "executor_unreachable"

# Child-process returncode substrings that mean "killed by a signal" (negative
# returncode). -9 = SIGKILL (the OOM killer's weapon), -6 = SIGABRT (abort,
# also seen under memory pressure / native crashes).
_OOM_RETURNCODE_MARKERS: tuple[str, ...] = (
    "exit code -9",
    "exit code -6",
)

# Exact exception class name raised at the executor transport boundary.
_EXECUTOR_UNREACHABLE_TYPE = "ExecutorUnreachableError"


def classify_executor_infra_failure(
    error_type: Optional[str], error_message: Optional[str]
) -> Optional[str]:
    """Return the executor-infrastructure reason for a platform-side failure,
    or ``None`` if the error is not one.

    Kept deliberately separate from ``classify_self_serviceable`` (disjoint
    concepts): this fires ONLY on an executor OOM (subprocess signal kill) or
    the typed ``ExecutorUnreachableError``. A user's LLM-provider connection
    error is NOT one of these — it is a transient the circuit breaker retries.
    """
    et = (error_type or "").strip()
    if et == _EXECUTOR_UNREACHABLE_TYPE:
        return EXECUTOR_INFRA_REASON_UNREACHABLE
    hay = f"{et}\n{error_message or ''}".lower()
    if not hay.strip():
        return None
    if any(marker in hay for marker in _OOM_RETURNCODE_MARKERS):
        return EXECUTOR_INFRA_REASON_OOM
    return None


# Per-reason owner-facing guidance for an executor-infra failure. Provider- and
# vendor-neutral (binding rule #15): state what happened and the user's next
# step (retry / split), never a model judgement or a force-stop.
EXECUTOR_INFRA_USER_MESSAGE: dict[str, str] = {
    EXECUTOR_INFRA_REASON_OOM: (
        "This turn could not run: the execution environment ran out of memory "
        "and was stopped by the system. This usually means the task or its "
        "tool outputs grew too large for one run. Try splitting it into smaller "
        "steps, or reduce the number of active modules, then send the message "
        "again."
    ),
    EXECUTOR_INFRA_REASON_UNREACHABLE: (
        "This turn could not run: your execution container is temporarily "
        "unreachable. It usually recovers automatically within a few seconds — "
        "please resend this message shortly."
    ),
}


def executor_infra_user_message(reason: str, raw_detail: str) -> str:
    """Compose the owner-facing message for an executor-infra failure:
    per-reason guidance plus the redacted transport detail so the concrete
    cause is visible, not hidden behind a black-box 'unknown'."""
    base = EXECUTOR_INFRA_USER_MESSAGE.get(
        reason,
        "This turn could not run due to a temporary platform-side execution "
        "issue. Please send the message again in a moment.",
    )
    detail = redact_secrets(raw_detail).strip()
    return f"{base}\n\nDetail: {detail}" if detail else base
