"""
@file_name: context_tools.py
@author: Bin Liang
@date: 2026-07-29
@description: Context self-service tools — current_time, context_status,
expand_capability, tool_search.

These hand the "context economics" to the agent itself instead of the
platform policing it (the constructive face of iron rules #14/#15).
``expand_capability`` is the framework-neutral dynamic-loading entry
(the framework knows Expandables, never "modules"); ``tool_search`` is
our own-algorithm, model-agnostic discovery over the deferred surface —
every user model gets it, no provider feature required.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Awaitable, Callable

from xyz_agent_context.agent_framework.nexus_power.contracts.tooling import (
    ToolAnnotations,
    ToolContext,
    ToolResult,
    ToolSpec,
)

# Injected capability seams (assembly wires the real ones).
ExpandFn = Callable[[str], Awaitable[str]]
StatusFn = Callable[[], dict[str, Any]]
SearchFn = Callable[[str], list[str]]


def specs(*, with_expansion: bool) -> list[ToolSpec]:
    read_only = ToolAnnotations(read_only=True)
    out = [
        ToolSpec(
            name="current_time",
            description=(
                "The current date and time with timezone. The prompt only "
                "carries the date; call this when you need the exact moment."
            ),
            input_schema={"type": "object", "properties": {}},
            annotations=read_only,
        ),
        ToolSpec(
            name="context_status",
            description=(
                "Your context economics so far this turn: tokens used, cache "
                "reads, and the model's context window — use it to decide "
                "when to be concise or wrap up cleanly."
            ),
            input_schema={"type": "object", "properties": {}},
            annotations=read_only,
        ),
        ToolSpec(
            name="tool_search",
            description=(
                "Search every available tool (in scope or expandable) by "
                "keyword. Call with no query for a grouped overview. Use it "
                "when you suspect a capability exists but don't see its tool."
            ),
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
            annotations=read_only,
        ),
    ]
    if with_expansion:
        out.append(
            ToolSpec(
                name="expand_capability",
                description=(
                    "Load one expandable capability into this turn by key "
                    "(see the capability list in your instructions). Returns "
                    "the capability's instructions; its tools become "
                    "available immediately. Costs context — expand what the "
                    "task needs. Idempotent per key; lasts for this turn."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "required": ["key"],
                },
            )
        )
    return out


class ContextToolHandlers:
    """Handlers bound to their injected seams (no globals)."""

    def __init__(
        self,
        *,
        expand: ExpandFn | None,
        status: StatusFn,
        search: SearchFn,
    ) -> None:
        self._expand = expand
        self._status = status
        self._search = search

    async def current_time(self, call_id: str, args: dict, ctx: ToolContext) -> ToolResult:
        now = _dt.datetime.now(_dt.timezone.utc).astimezone()
        return ToolResult(call_id=call_id, ok=True, content=now.isoformat(timespec="seconds"))

    async def context_status(self, call_id: str, args: dict, ctx: ToolContext) -> ToolResult:
        return ToolResult(call_id=call_id, ok=True, content=self._status())

    async def tool_search(self, call_id: str, args: dict, ctx: ToolContext) -> ToolResult:
        lines = self._search(str(args.get("query", "")).strip())
        return ToolResult(
            call_id=call_id, ok=True, content="\n".join(lines) or "(no matches)"
        )

    async def expand_capability(self, call_id: str, args: dict, ctx: ToolContext) -> ToolResult:
        if self._expand is None:
            return ToolResult(call_id=call_id, ok=False, error="no expandable catalog this turn")
        key = str(args.get("key", "")).strip()
        if not key:
            return ToolResult(call_id=call_id, ok=False, error="`key` is required")
        try:
            instructions = await self._expand(key)
        except KeyError:
            return ToolResult(call_id=call_id, ok=False, error=f"unknown capability key: {key}")
        return ToolResult(call_id=call_id, ok=True, content=instructions)

    def handlers(self) -> dict[str, Any]:
        return {
            "current_time": self.current_time,
            "context_status": self.context_status,
            "tool_search": self.tool_search,
            "expand_capability": self.expand_capability,
        }
