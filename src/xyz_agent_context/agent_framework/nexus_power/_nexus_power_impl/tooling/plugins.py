"""
@file_name: plugins.py
@author: Bin Liang
@date: 2026-07-29
@description: Plugin registration surface (P3 seat) — in-process
extensions registering tools / hooks / dynamic-tail prompt sections
through one typed API (a synthesis of pi's ExtensionAPI and OpenClaw's
plugin manifests). MCP is protocol-level attachment; plugins are
process-level. Both converge on the ToolChannel ecosystem — same
dispatcher, same policy, same log; plugins own no back doors.

Security boundary: cloud plugins are platform-allowlisted (multi-tenant
never executes arbitrary user code); desktop may load local directories
behind an explicit feature gate (iron rule #7 keeps both modes stated).
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from xyz_agent_context.agent_framework.nexus_power.contracts.tooling import (
    ToolContext,
    ToolResult,
    ToolSpec,
)


class PluginApi:
    """What a plugin receives at load time (its entire surface)."""

    def register_tool(self, spec: ToolSpec, handler: Callable[..., Awaitable[Any]]) -> None:
        raise NotImplementedError("PluginApi ships in P3")

    def register_hook(self, event: Any, fn: Callable[..., Awaitable[Any]], *, failure: str) -> None:
        raise NotImplementedError("PluginApi ships in P3")

    def register_prompt_section(self, section_fn: Callable[..., str]) -> None:
        # Dynamic tail ONLY — plugins may never touch the stable prefix
        # (cache constraint C2 as a hard boundary).
        raise NotImplementedError("PluginApi ships in P3")


class PluginChannel:
    """Aggregated plugin-registered tools as one ToolChannel (P3)."""

    def __init__(self, manifests: tuple[Any, ...]) -> None:
        self._manifests = manifests

    def list_tools(self) -> list[ToolSpec]:
        raise NotImplementedError("PluginChannel ships in P3")

    async def call(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        raise NotImplementedError("PluginChannel ships in P3")

    async def refresh(self) -> bool:
        raise NotImplementedError("PluginChannel ships in P3")
