"""
@file_name: limits.py
@author:
@date: 2026-10-08
@description: Size bounds on model-supplied browser tool arguments, in one place.

Every other data path in the browser is bounded — user input on the stream
(``stream_bridge.MAX_INPUT_BYTES``), screenshots (``visual``), page text
(``read.DEFAULT_TEXT_LIMIT``). These close the remaining one: what the agent
writes INTO the page. Each argument is embedded in a single CDP
``Runtime.evaluate`` message, so an unbounded string (a model looping, or
pasting a whole page into a form field) becomes one multi-megabyte frame in
the MCP host, which serves every agent's tools.

Values are generous for real use: form text matches the stream's own 64 KiB
input bound; selectors allow deep generated ``nth-of-type`` paths.
"""
from __future__ import annotations

from typing import Any

#: Longest CSS selector accepted (generated ancestor paths stay well below this).
MAX_SELECTOR_CHARS = 4096
#: Longest fill text / select value, in characters (matches the stream's 64 KiB
#: user-input bound numerically; tool descriptions state it in characters).
MAX_FIELD_TEXT_CHARS = 64 * 1024
#: Longest ``browser_run`` expression.
MAX_SCRIPT_CHARS = 64 * 1024


def check_selector(selector: Any) -> None:
    """Raise ValueError unless ``selector`` is None or a bounded nonempty string."""
    if selector is None:
        return
    if not isinstance(selector, str) or not selector.strip():
        raise ValueError("selector must be a nonempty CSS selector")
    if len(selector) > MAX_SELECTOR_CHARS:
        raise ValueError(f"selector is longer than {MAX_SELECTOR_CHARS} characters; use a shorter selector")


def check_text(name: str, value: Any, limit: int) -> None:
    """Raise ValueError unless ``value`` is None or a string of at most ``limit`` characters."""
    if value is None:
        return
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if len(value) > limit:
        raise ValueError(f"{name} is longer than {limit} characters; split it into smaller steps")
