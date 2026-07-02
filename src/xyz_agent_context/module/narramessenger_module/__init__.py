"""
@file_name: __init__.py
@date: 2026-06-17
@description: NarraMessenger channel module package — re-exports NarramessengerModule.
"""

try:
    from .narramessenger_module import NarramessengerModule

    __all__ = ["NarramessengerModule"]
except ImportError:  # pragma: no cover — bootstrap during dependency build-out
    __all__ = []
