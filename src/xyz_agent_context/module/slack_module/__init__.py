"""
@file_name: __init__.py
@date: 2026-05-08
@description: Slack channel module package — re-exports SlackModule.
"""

# SlackModule is added once Task 8 (slack_module.py) is in place.
try:
    from .slack_module import SlackModule

    __all__ = ["SlackModule"]
except ImportError:  # pragma: no cover — bootstrap during dependency build-out
    __all__ = []
