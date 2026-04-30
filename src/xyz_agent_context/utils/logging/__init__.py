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
]
