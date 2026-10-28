"""
@file_name: runtime.py
@author:
@date: 2026-09-22
@description: Detect whether the in-app browser runtime (Chromium) is usable.

The runtime is NOT shipped in the dmg (Owner decision 2026-09-22): the user
installs it once from the UI, the way Claude Code / Codex handle their own
runtime dependencies. Every entry point that needs a browser therefore has to
ask "is it there?" first, and three of them branch on the answer:

  * the agent-facing MCP tools — absent means ``NEEDS_HUMAN``, never a raw
    error the user cannot act on;
  * ``UrlRenderer``'s ``stream`` branch — absent renders the install card
    instead of a blank canvas;
  * ``hook_data_gathering`` — so the agent knows before it tries.

Two deliberate choices:

**Presence on disk is not readiness.** A half-extracted download and a
Gatekeeper-quarantined binary both leave a file that cannot start. We only
report ``ready`` when a ``--version`` probe actually answered, because the
alternative failure (a blank panel, no diagnosis) is the one users cannot
get themselves out of.

**Nothing here is cached.** The status is recomputed per call: the user can
delete the runtime between two uses, and a stale ``ready`` would send the
next call into the same undiagnosable blank panel.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Optional

#: ``absent`` — unusable, offer install. ``installing`` — download/extract in
#: flight. ``ready`` — probed and answered.
RuntimeState = Literal["absent", "installing", "ready"]

#: Why the state is what it is. Surfaced to the UI so an install card can say
#: something more useful than "not available".
RuntimeReason = Literal["no-executable", "probe-failed", "installing", "ready"]


@dataclass(frozen=True)
class BrowserRuntimeStatus:
    """The single source of truth for "can we open a browser right now?"."""

    state: RuntimeState
    reason: RuntimeReason
    executable: Optional[Path] = None
    version: Optional[str] = None

    def to_dict(self) -> dict:
        """JSON-ready form for the HTTP API and the ContextData hook."""
        return {
            "state": self.state,
            "reason": self.reason,
            "version": self.version,
            "executable": str(self.executable) if self.executable is not None else None,
        }


def classify_runtime(
    *,
    executable: Optional[Path],
    version: Optional[str],
    installing: bool,
) -> BrowserRuntimeStatus:
    """Decision table for the runtime state. Pure; unit-tested exhaustively.

    Args:
        executable: What the locator found, or None.
        version: What the ``--version`` probe returned. None or blank means the
            probe did not answer — treated as failure, see module docstring.
        installing: Whether an install is currently in flight.

    Returns:
        The status. ``ready`` outranks ``installing`` because install is
        idempotent and a usable runtime must never be hidden
        behind a progress bar.
    """
    probed = bool(version and version.strip())
    if executable is not None and probed:
        return BrowserRuntimeStatus(
            state="ready", reason="ready", executable=executable, version=version
        )
    if installing:
        return BrowserRuntimeStatus(
            state="installing", reason="installing", executable=executable
        )
    if executable is None:
        return BrowserRuntimeStatus(state="absent", reason="no-executable")
    return BrowserRuntimeStatus(state="absent", reason="probe-failed", executable=executable)


def detect_runtime(
    *,
    locate: Callable[[], Optional[Path]],
    probe: Callable[[Path], Optional[str]],
    installing: bool,
) -> BrowserRuntimeStatus:
    """Run the two IO seams, then classify.

    Both seams are injected rather than imported so the decision table can be
    driven deterministically in tests, and so a future backend that resolves
    its browser differently (a container, a remote grid) reuses this without
    inheriting our on-disk assumptions.

    Neither seam is allowed to propagate: a locator that cannot read the
    filesystem and a probe the OS killed are both *absent*, and whoever asked
    for the status (an agent turn, a render request) must get an answer rather
    than an exception.

    Args:
        locate: Finds the browser executable, or returns None.
        probe: Starts ``executable --version`` and returns its output.
        installing: Whether an install is currently in flight.
    """
    try:
        executable = locate()
    except Exception:
        executable = None

    version: Optional[str] = None
    if executable is not None:
        try:
            version = probe(executable)
        except Exception:
            version = None

    return classify_runtime(executable=executable, version=version, installing=installing)
