"""
@file_name: mcp_channel.py
@author: Bin Liang
@date: 2026-07-29
@description: The MCP client channel — platform capabilities (and any
external MCP server) enter here. In v1 this includes the reply tools,
so it is minimal-set core, not an extension.

Contract points:
  - tool names keep the ``mcp__{server}__{tool}`` namespace (three
    legacy consumers substring-match it);
  - per-server SSE sessions with per-agent headers, connected
    concurrently; one failing server degrades to absent tools (logged),
    never a turn abort;
  - ``add_servers`` is the dynamic-expansion endpoint: append-only tool
    inventory (cache discipline), generation counter bumps so the
    dispatcher's cache invalidates, name collisions resolve
    first-registration-wins with a warning;
  - ``aclose`` reaps every session — orphaned connections are a known
    incident class.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from typing import Any

from loguru import logger

from xyz_agent_context.agent_framework.nexus_power.contracts.model import McpServerSpec
from xyz_agent_context.agent_framework.nexus_power.contracts.tooling import (
    ToolAnnotations,
    ToolContext,
    ToolResult,
    ToolSpec,
)

_CONNECT_TIMEOUT_S = 15
_CALL_TIMEOUT_S = 300


def mcp_tool_name(server: str, tool: str) -> str:
    return f"mcp__{server}__{tool}"


class McpToolChannel:
    """Aggregated MCP servers as one ToolChannel."""

    def __init__(self, servers: dict[str, McpServerSpec]) -> None:
        self._pending: dict[str, McpServerSpec] = dict(servers)
        self._sessions: dict[str, Any] = {}
        self._stack = AsyncExitStack()
        self._specs: list[ToolSpec] = []
        self._route: dict[str, tuple[str, str]] = {}  # full name -> (server, tool)
        self.generation = 0
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        """Connect every pending server concurrently; failures degrade.

        Registration is SEPARATED from connection: the gather completes
        in arbitrary order, so specs are registered afterwards, batch by
        batch, in server-name order. That makes the tool array a
        deterministic function of the connect batches (constraint C2:
        later batches append after earlier ones, never interleave)."""
        async with self._lock:
            pending, self._pending = self._pending, {}
            if not pending:
                return
            ordered = sorted(pending.items())
            results = await asyncio.gather(
                *(self._connect_one(name, spec) for name, spec in ordered),
                return_exceptions=True,
            )
            for (name, _), result in zip(ordered, results):
                if isinstance(result, BaseException):
                    logger.warning(f"MCP server '{name}' unavailable: {result}")
                    continue
                session, listed_tools = result
                self._sessions[name] = session
                self._register_tools(name, listed_tools)
            self.generation += 1

    async def _connect_one(self, name: str, spec: McpServerSpec):
        """Connect one server and return ``(session, listed_tools)`` —
        no shared-state writes here (the caller registers in order)."""
        from mcp import ClientSession
        from mcp.client.sse import sse_client

        read, write = await asyncio.wait_for(
            self._stack.enter_async_context(
                sse_client(spec.url, headers=spec.headers or None)
            ),
            timeout=_CONNECT_TIMEOUT_S,
        )
        session = await self._stack.enter_async_context(ClientSession(read, write))
        await asyncio.wait_for(session.initialize(), timeout=_CONNECT_TIMEOUT_S)
        listed = await asyncio.wait_for(session.list_tools(), timeout=_CONNECT_TIMEOUT_S)
        return session, list(listed.tools)

    def _register_tools(self, name: str, tools: list[Any]) -> None:
        for tool in tools:
            full_name = mcp_tool_name(name, tool.name)
            if full_name in self._route:
                logger.warning(
                    f"MCP tool name collision on {full_name!r}: first "
                    f"registration wins, later one ignored"
                )
                continue
            annotations = ToolAnnotations(
                read_only=bool(getattr(tool.annotations, "readOnlyHint", False))
                if tool.annotations
                else False,
                destructive=bool(getattr(tool.annotations, "destructiveHint", False))
                if tool.annotations
                else False,
            )
            self._specs.append(
                ToolSpec(
                    name=full_name,
                    description=tool.description or "",
                    input_schema=tool.inputSchema or {"type": "object"},
                    annotations=annotations,
                )
            )
            self._route[full_name] = (name, tool.name)

    async def add_servers(self, servers: dict[str, McpServerSpec]) -> None:
        """Dynamic expansion endpoint: connect and APPEND (never resort)."""
        async with self._lock:
            for name, spec in servers.items():
                if name in self._sessions:
                    continue
                self._pending[name] = spec
        await self.connect()

    # ---- ToolChannel ----

    def list_tools(self) -> list[ToolSpec]:
        return list(self._specs)

    async def call(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        route = self._route.get(name)
        if route is None:
            return ToolResult(call_id="", ok=False, error=f"unknown MCP tool {name!r}")
        server, tool = route
        session = self._sessions.get(server)
        if session is None:
            return ToolResult(call_id="", ok=False, error=f"MCP server '{server}' is not connected")
        try:
            result = await asyncio.wait_for(
                session.call_tool(tool, args), timeout=_CALL_TIMEOUT_S
            )
        except Exception as exc:  # noqa: BLE001 - tool errors are results
            return ToolResult(call_id="", ok=False, error=f"MCP call failed: {exc}")
        text = _render_content(result)
        if getattr(result, "isError", False):
            return ToolResult(call_id="", ok=False, error=text or "tool reported an error")
        return ToolResult(call_id="", ok=True, content=text)

    async def refresh(self) -> bool:
        """v1: no list_changed subscription; expansion drives changes via
        ``add_servers`` (which bumps the generation itself)."""
        return False

    async def aclose(self) -> None:
        try:
            await self._stack.aclose()
        except Exception as exc:  # noqa: BLE001 - closing must not raise
            logger.warning(f"MCP channel close: {exc}")
        self._sessions.clear()


def _render_content(result: Any) -> str:
    parts: list[str] = []
    for item in getattr(result, "content", None) or ():
        text = getattr(item, "text", None)
        if text is not None:
            parts.append(str(text))
        else:
            parts.append(str(item))
    return "\n".join(parts)
