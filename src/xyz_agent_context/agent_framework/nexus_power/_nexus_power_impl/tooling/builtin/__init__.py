"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-07-29
@description: BuiltinToolset — the aggregated builtin ToolChannel.

Boundary (Owner decision): builtins are THINKING PRIMITIVES only —
files, execution, web, context self-service. Outward expression is not
a basic function: speaking tools are platform-granted (via MCP) and the
framework owns none. An agent without channels is mute, not broken.

Groups are feature-gated by the assembly (``TurnOptions.builtin_groups``);
ungated tools never appear in a schema (schema honesty — no
registered-but-dead surface).
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from xyz_agent_context.agent_framework.nexus_power.contracts.tooling import (
    ToolContext,
    ToolResult,
    ToolSpec,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.tooling.builtin import (
    context_tools as _context_tools,
    files as _files,
    shell as _shell,
)

Handler = Callable[[str, dict, ToolContext], Awaitable[ToolResult]]


class BuiltinToolset:
    """The builtin ToolChannel (groups: files / shell / context)."""

    def __init__(
        self,
        ctx: ToolContext,
        *,
        enabled_groups: frozenset[str],
        context_handlers: "_context_tools.ContextToolHandlers | None" = None,
        with_expansion: bool = False,
    ) -> None:
        self._ctx = ctx
        self._specs: list[ToolSpec] = []
        self._handlers: dict[str, Handler] = {}
        if "files" in enabled_groups:
            self._mount(_files.specs(), _files.HANDLERS)
        if "shell" in enabled_groups:
            self._mount(_shell.specs(), _shell.HANDLERS)
        if "context" in enabled_groups and context_handlers is not None:
            self._mount(
                _context_tools.specs(with_expansion=with_expansion),
                context_handlers.handlers(),
            )

    def _mount(self, specs: list[ToolSpec], handlers: dict[str, Any]) -> None:
        for spec in specs:
            if spec.name in handlers:
                self._specs.append(spec)
                self._handlers[spec.name] = handlers[spec.name]

    # ---- ToolChannel ----

    def list_tools(self) -> list[ToolSpec]:
        return list(self._specs)

    async def call(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        handler = self._handlers.get(name)
        if handler is None:
            return ToolResult(call_id="", ok=False, error=f"unknown builtin tool {name!r}")
        return await handler("", args, ctx)

    async def refresh(self) -> bool:
        return False
