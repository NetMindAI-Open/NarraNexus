"""
@file_name: _common_tools_mcp_tools.py
@author: Bin Liang
@date: 2026-04-17
@description: MCP server factory + env-based web_search backend dispatch.

This file is the thin entry point for CommonToolsModule's MCP server.
It defines the shared ``with_mcp_timeout`` decorator and, at server
creation time, picks exactly one web_search backend based on
environment:

  - BRAVE_API_KEY set → web_search_brave_tool.register(mcp, api_key)
  - otherwise         → web_search_ddgs_tool.register(mcp)

The registered tool is always named ``web_search`` — LLM prompts are
environment-agnostic.
"""

import asyncio
import functools
import os
from typing import Any, Callable, Awaitable

from loguru import logger
from mcp.server.fastmcp import FastMCP


def with_mcp_timeout(
    seconds: float,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Hard-cap an MCP tool handler's execution time.

    Wraps the handler in ``asyncio.wait_for``. On timeout, returns a
    structured error payload instead of letting the coroutine hang
    forever. Returns a string because FastMCP validates tool output
    against the wrapped function's return annotation and our MCP
    tools that need hard timeouts all return ``str``.

    Usage:
        @mcp.tool()
        @with_mcp_timeout(45)
        async def my_tool(...) -> str:
            ...

    Note: only bounds the awaiting coroutine, not spawned threads or
    subprocesses. For tools that wrap sync network libraries, prefer
    subprocess isolation (see web_search_ddgs_tool) on top of this.
    """

    def _deco(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(fn)
        async def _wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await asyncio.wait_for(fn(*args, **kwargs), timeout=seconds)
            except asyncio.TimeoutError:
                msg = (
                    f"{fn.__name__} timed out after {seconds}s. "
                    "The tool is temporarily unavailable — try a different "
                    "approach or retry later."
                )
                logger.exception(f"[MCP timeout] {msg}")
                return f"[tool_error] {msg}"

        return _wrapper

    return _deco


def create_common_tools_mcp_server(port: int) -> FastMCP:
    """Create the CommonToolsModule MCP server with the env-appropriate backend."""
    mcp = FastMCP("common_tools_module")
    mcp.settings.port = port

    brave_key = os.environ.get("BRAVE_API_KEY", "").strip()
    if brave_key:
        from ._common_tools_impl.web_search_brave_tool import register as register_brave
        register_brave(mcp, api_key=brave_key)
        logger.info("CommonTools MCP: web_search backend = Brave")
    else:
        from ._common_tools_impl.web_search_ddgs_tool import register as register_ddgs
        register_ddgs(mcp)
        logger.info(
            "CommonTools MCP: web_search backend = DDGS (no BRAVE_API_KEY)"
        )

    return mcp
