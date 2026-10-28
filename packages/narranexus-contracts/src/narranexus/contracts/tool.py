"""
@file_name: tool.py
@author: Bin Liang
@date: 2026-09-03
@description: Contract for tool contributions (slot ``agent.capabilities.tools``).

A tool provider lists tools deterministically (same order every call; the
turn's tool surface is append-only so prompt prefixes stay byte-stable).
Plugin tools are reachable through ``tool_search`` by default; only
``always_visible`` tools are put in the model's up-front tool list, because
every visible tool costs context on every turn.

Contract version: ``API_VERSIONS["tool"]``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    # JSON-schema object for the arguments; empty means no arguments.
    input_schema: Mapping[str, Any] = field(default_factory=dict)
    always_visible: bool = False
    # MCP server that serves the tool; empty means the provider serves it in-process.
    server: str = ""

    def __post_init__(self) -> None:
        if not self.name or any(ch.isspace() for ch in self.name):
            raise ValueError(f"tool name must be a non-empty token, got {self.name!r}")


@runtime_checkable
class ToolProvider(Protocol):
    """Lists tools; must be deterministic and cheap (called per turn)."""

    def list_tools(self) -> Sequence[ToolSpec]: ...


__all__ = ["ToolProvider", "ToolSpec"]
