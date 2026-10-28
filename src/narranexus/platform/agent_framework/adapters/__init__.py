"""
@file_name: __init__.py
@author: NetMind.AI
@date: 2026-07-24
@description: Agent-framework adapters (binding rule #9's swap seam): one
subpackage per framework (claude/, codex/), the OpenAI-agents caller,
and the shared PreToolUse policy guard.

One re-export: `build_tool_policy_guard`. Framework adapters are plugins
since batch 6b (`builtin.frameworks.claude_code` ships its own wheel), and a
framework adapter is exactly who must install the shared PreToolUse guard —
so the builder is public while the guard's internals stay private.

Lazy (PEP 562): the guard module pulls the tool-policy schema, which an
adapter that does not use it should not pay for.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ._tool_policy_guard import build_tool_policy_guard


def __getattr__(name: str):
    """Resolve `build_tool_policy_guard` lazily (PEP 562)."""
    if name != "build_tool_policy_guard":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from ._tool_policy_guard import build_tool_policy_guard as _b

    globals()["build_tool_policy_guard"] = _b
    return _b


def __dir__() -> list[str]:
    return sorted({"build_tool_policy_guard"} | set(globals()))


__all__ = ["build_tool_policy_guard"]
