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
from typing import Union

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
