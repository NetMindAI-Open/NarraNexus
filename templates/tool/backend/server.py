"""Stdio MCP server for __DISPLAY_NAME__ (started by the host per McpServerSpec)."""
from __future__ import annotations

from fastmcp import FastMCP

mcp = FastMCP("__PLUGIN_PKG__")


@mcp.tool()
def __PLUGIN_PKG___echo(text: str) -> str:
    return text


if __name__ == "__main__":
    mcp.run()
