"""
@file_name: __init__.py
@date: 2026-05-09
@description: Telegram channel module package — re-exports TelegramModule.
"""

# TelegramModule is added once Task 7 (telegram_module.py) is in place.
try:
    from .telegram_module import TelegramModule

    __all__ = ["TelegramModule"]
except ImportError:  # pragma: no cover — bootstrap during dependency build-out
    __all__ = []
