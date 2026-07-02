"""
@file_name: __init__.py
@date: 2026-06-16
@description: Discord channel module package — re-exports DiscordModule.
"""

try:
    from .discord_module import DiscordModule

    __all__ = ["DiscordModule"]
except ImportError:  # pragma: no cover — bootstrap during dependency build-out
    __all__ = []
