"""
@file_name: cancellation_view.py
@author: Bin Liang
@date: 2026-07-27
@description: Uniform read-side view over cooperative-cancellation tokens.

Drivers receive ``cancellation`` as an opaque ``Any`` and historically
guessed its API: the remote driver read ``token.is_cancelled`` (the real
``CancellationToken`` bool property), while CodexSDKv2 called
``token.is_set()`` (asyncio.Event style) — which a real token does not
have, so that check was dead code and in-process codex turns could not
be interrupted. This module is the single place that knows every token
shape; drivers ask one question: ``view.requested()``.

Read-side only by design: firing a cancellation stays on the owner
(``CancellationToken.cancel()`` in agent_runtime); drivers must never
cancel, only observe (iron rule #15 — the platform must not become the
interruption source).
"""

from __future__ import annotations

from typing import Any


class CancellationView:
    """Normalizes ``is_cancelled`` / ``is_set()`` / ``None`` into one check.

    Precedence: the documented ``CancellationToken.is_cancelled`` bool
    property is authoritative; ``is_set()`` (asyncio.Event style) is the
    fallback for event-shaped tokens; anything else never cancels.
    """

    __slots__ = ("_token",)

    def __init__(self, token: Any | None) -> None:
        self._token = token

    def requested(self) -> bool:
        """True the moment the underlying token has been cancelled."""
        token = self._token
        if token is None:
            return False
        is_cancelled = getattr(token, "is_cancelled", None)
        if is_cancelled is not None and not callable(is_cancelled):
            return bool(is_cancelled)
        is_set = getattr(token, "is_set", None)
        if callable(is_set):
            return bool(is_set())
        return False
