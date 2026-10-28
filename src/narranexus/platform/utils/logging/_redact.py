"""
@file_name: _redact.py
@author: Bin Liang
@date: 2026-04-28
@description: Redaction helpers for sensitive log fields.

Strips token / password / secret values before they reach a sink.
Recurses into nested dicts and sequences. Detects values shaped like a
JWT and truncates them so a snippet survives for correlation while the
full credential is destroyed.
"""
from __future__ import annotations

import re
from typing import Any


_SENSITIVE_KEYS: frozenset[str] = frozenset({
    "token",
    "password",
    "passwd",
    "app_secret",
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "jwt",
    "secret",
    "access_token",
    "refresh_token",
})

_JWT_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}$")


def redact(value: Any) -> Any:
    """Return a copy of *value* with sensitive content masked.

    Rules:
      - dict: keys whose lowercase form is in the sensitive set →
        replaced with ``"***"``. Other values recurse.
      - list / tuple: each element recurses; original sequence type
        preserved.
      - str shaped like a JWT (three base64url segments separated by
        dots, each ≥ 8 chars) → first 8 chars + ``"..."``.
      - everything else: returned unchanged.

    The caller's input is never mutated.
    """
    if isinstance(value, dict):
        return {
            k: ("***" if str(k).lower() in _SENSITIVE_KEYS else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact(v) for v in value)
    if isinstance(value, str) and _JWT_PATTERN.match(value):
        return value[:8] + "..."
    return value
