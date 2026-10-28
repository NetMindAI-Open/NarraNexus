"""
@file_name: errors.py
@author: NarraNexus
@date: 2026-08-28
@description: Turns a raw pip/npm subprocess failure into a structured,
              bilingual message the Settings UI can render as-is.

Users hitting this are almost always behind the Great Firewall (registry.
npmjs.org / pypi.org throttled to a crawl), running the desktop app without
write access to ~/.narranexus/plugins, or fully offline. Those three cases
account for nearly every real-world install failure, so they get a specific,
actionable ``kind`` + English message; everything else falls back to a
generic "unknown" rather than guessing. Messages are English-only (project
binding rule: no Chinese strings in code) — the Settings UI localizes by
switching on ``kind``, not by parsing ``message``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from ._installers.base import PluginInstallSubprocessError

ErrorKind = Literal["registry_slow", "permission_denied", "network_blocked", "unknown"]


class PluginBusyError(RuntimeError):
    """Raised when an install/uninstall is refused because the same plugin is
    already mid-operation.

    Install and uninstall share one per-plugin lock, so kicking off either
    while the other is running would race two package managers on the same
    target directory. The route layer maps this to HTTP 409 (try again once
    the in-flight operation finishes). ``plugin_id`` is exposed so the caller
    can name it back to the user.
    """

    def __init__(self, plugin_id: str, message: str) -> None:
        self.plugin_id = plugin_id
        super().__init__(message)


@dataclass(frozen=True)
class PluginError:
    """A classified install failure, ready to show a user."""

    kind: ErrorKind
    message: str


# Order matters: checked top to bottom, first match wins. A DNS/connection
# failure and a bare timeout can both appear in the same npm/pip error text,
# so the more specific "cannot reach at all" patterns are checked first.
_NETWORK_BLOCKED_PATTERNS = (
    re.compile(r"getaddrinfo enotfound", re.IGNORECASE),
    re.compile(r"name or service not known", re.IGNORECASE),
    re.compile(r"could not fetch url", re.IGNORECASE),
    re.compile(r"connection refused", re.IGNORECASE),
    re.compile(r"network is unreachable", re.IGNORECASE),
)

_PERMISSION_PATTERNS = (
    re.compile(r"eacces", re.IGNORECASE),
    re.compile(r"permission denied", re.IGNORECASE),
    re.compile(r"errno 13", re.IGNORECASE),
)

_REGISTRY_SLOW_PATTERNS = (
    re.compile(r"network timeout", re.IGNORECASE),
    re.compile(r"etimedout", re.IGNORECASE),
    re.compile(r"read timed out", re.IGNORECASE),
    re.compile(r"connection timed out", re.IGNORECASE),
)

_MESSAGES: dict[ErrorKind, str] = {
    "registry_slow": (
        "Install timed out — the official registry may be slow from your network. "
        "Try switching to a mirror (npmmirror for npm, a domestic index for pip) and retry."
    ),
    "permission_denied": (
        "Permission denied writing to the plugin directory — check the ownership "
        "and permissions of ~/.narranexus/plugins."
    ),
    "network_blocked": (
        "Could not reach the package registry — check your network connection or proxy settings."
    ),
    "unknown": "Install failed for an unrecognized reason — check the detailed log.",
}


def _classify_text(text: str) -> ErrorKind:
    for pattern in _NETWORK_BLOCKED_PATTERNS:
        if pattern.search(text):
            return "network_blocked"
    for pattern in _PERMISSION_PATTERNS:
        if pattern.search(text):
            return "permission_denied"
    for pattern in _REGISTRY_SLOW_PATTERNS:
        if pattern.search(text):
            return "registry_slow"
    return "unknown"


def classify_error(exc: BaseException) -> PluginError:
    """Map any exception raised by an install attempt to a PluginError.

    ``PluginInstallSubprocessError`` carries the real stdout+stderr text, so
    it is pattern-matched. Any other exception (e.g. ``FileNotFoundError``
    when npm/pip itself is missing) falls back to "unknown" with a generic
    but still actionable message, rather than raising.
    """
    if isinstance(exc, PluginInstallSubprocessError):
        kind = _classify_text(exc.output)
    else:
        kind = _classify_text(str(exc))
    return PluginError(kind=kind, message=_MESSAGES[kind])
