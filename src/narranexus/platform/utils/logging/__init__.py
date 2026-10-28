"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-04-28
@description: Public surface of the unified logging package.

Every process should call ``setup_logging`` exactly once at startup and
then import only the four names below from the rest of the codebase.
``loguru.logger`` itself remains directly importable for plain log
calls (we don't re-export it on purpose — that would let callers think
they need our package to log at all, which they don't).
"""
from ._context import bind_event
from ._redact import redact
from ._setup import setup_logging
from ._timing import timed

__all__ = [
    "setup_logging",
    "bind_event",
    "timed",
    "redact",
    "telemetry_consent",
    "set_telemetry_optout",
]


def telemetry_consent() -> dict:
    """Telemetry consent state for the settings surface — see _ship.
    Lazy import: _ship pulls in httpx, and an import-time failure there
    must break shipping only, never the logging package."""
    from ._ship import telemetry_consent as _impl

    return _impl()


def set_telemetry_optout(opted_out: bool) -> None:
    """Write/remove the per-machine telemetry opt-out marker — see _ship."""
    from ._ship import set_telemetry_optout as _impl

    _impl(opted_out)
