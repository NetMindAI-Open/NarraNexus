"""
@file_name: mcp_server.py
@author: Bin Liang
@date: 2026-09-03
@description: Contract for MCP server contributions (slot ``agent.capabilities.mcp_servers``).

Site-level MCP servers a plugin adds to every agent's tool surface, next to
the per-agent servers a user configures. ``stdio`` servers are started by
the host (command + args, environment restricted to what the spec names);
``sse`` / ``streamable_http`` servers are reached by URL.

Contract version: ``API_VERSIONS["mcp_server"]``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping

Transport = Literal["stdio", "sse", "streamable_http"]


@dataclass(frozen=True)
class McpServerSpec:
    name: str
    transport: Transport
    url: str = ""
    command: str = ""
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    headers: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("mcp server name is required")
        if self.transport == "stdio":
            if not self.command or self.url:
                raise ValueError(f"stdio server {self.name!r} needs a command and no url")
        elif self.transport in ("sse", "streamable_http"):
            if not self.url or self.command:
                raise ValueError(f"{self.transport} server {self.name!r} needs a url and no command")
        else:
            raise ValueError(f"unknown transport {self.transport!r}")

    def to_config(self) -> dict[str, object]:
        """The mapping the agent frameworks consume (same shape as user-configured servers)."""
        if self.transport == "stdio":
            return {"type": "stdio", "command": self.command, "args": list(self.args), "env": dict(self.env)}
        cfg: dict[str, object] = {"type": self.transport, "url": self.url}
        if self.headers:
            cfg["headers"] = dict(self.headers)
        return cfg


__all__ = ["McpServerSpec", "Transport"]
