"""
@file_name: _common_tools_mcp_tools.py
@author: Bin Liang
@date: 2026-04-17
@description: MCP server + tool definitions for CommonToolsModule

Tools exposed:
- web_search(queries, max_results_per_query): DuckDuckGo search, multi-query

Stateless — tools take plain arguments, no agent_id / user_id bookkeeping.

Handler-level hard timeout (Bug 20, 2026-04-20)
------------------------------------------------
Every tool registered here is wrapped with ``with_mcp_timeout`` so no
single tool invocation can wedge the shared MCP container. Triple
defense: web_search.py has internal per-query + overall wait_for, and
this decorator is the final outer bound. The 2026-04-18 incident
showed that "one sync library hanging at the C layer" can cascade into
the whole event loop if NO layer has a timeout — this decorator
ensures there is always one.
"""

import asyncio
import functools
from typing import Any, Callable, Awaitable

from loguru import logger
from mcp.server.fastmcp import FastMCP


def with_mcp_timeout(
    seconds: float,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Hard-cap an MCP tool handler's execution time.

    Wraps the handler in ``asyncio.wait_for``. On timeout, returns a
    structured error payload instead of letting the coroutine hang
    forever. The LLM receives "tool timed out" as a normal tool result
    and can pick an alternative, rather than the whole agent loop
    sitting silent until the SDK's idle timer fires.

    Usage:
        @mcp.tool()
        @with_mcp_timeout(45)
        async def my_tool(...) -> dict:
            ...

    Note: this only bounds the *awaiting* coroutine, not any worker
    threads it spawned. Leaked threads are a residual Python limit —
    see web_search.py header comment.
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
                logger.error(f"[MCP timeout] {msg}")
                # Return a string — FastMCP validates tool output against the
                # wrapped function's return annotation, and our MCP tools
                # that need hard timeouts all return str (web_search, future
                # tools following the same pattern). If a tool returns a
                # richer type later, extend this decorator to consult the
                # annotation and format accordingly.
                return f"[tool_error] {msg}"

        return _wrapper

    return _deco


# Outer handler cap. Must exceed web_search.OVERALL_TIMEOUT_S (30s) by
# enough to let the inner layer report its own timeouts cleanly.
_WEB_SEARCH_HANDLER_TIMEOUT_S = 45.0


def create_common_tools_mcp_server(port: int) -> FastMCP:
    mcp = FastMCP("common_tools_module")
    mcp.settings.port = port

    @mcp.tool()
    @with_mcp_timeout(_WEB_SEARCH_HANDLER_TIMEOUT_S)
    async def web_search(
        queries: list[str],
        max_results_per_query: int = 5,
    ) -> str:
        """Search the web via DuckDuckGo and return the top hits.

        Accepts a **list** of queries and runs them in parallel — pass multiple
        queries when you want to cover different angles in a single round trip.

        Each entry in `queries` can be EITHER:
        - A natural-language question (e.g. "How does Python asyncio gather handle exceptions?")
        - A set of keywords (e.g. "python asyncio gather exception propagation")

        Use whichever form is more likely to match how the information is written
        on the web. For factual lookups, keywords often work better; for
        reasoning/"how/why" questions, full sentences often retrieve better pages.

        Args:
            queries: List of search queries. Empty strings are dropped.
                Recommended: 1–5 queries per call. DuckDuckGo will rate-limit
                aggressive fan-out.
            max_results_per_query: Max hits per query. Default 5, hard cap 10.

        Returns:
            Markdown-formatted results grouped by query. Each hit has title,
            URL, and a short snippet. If a query fails or times out, the
            error is reported inline without breaking the other queries.
        """
        from xyz_agent_context.module.common_tools_module._common_tools_impl.web_search import (
            search_many,
            format_results,
        )

        try:
            bundles = await search_many(queries, max_results_per_query)
        except Exception as e:  # defensive — search_many already swallows per-query errors
            logger.error(f"CommonToolsMCP: web_search top-level crash: {e}")
            return f"web_search failed: {e}"

        logger.info(
            f"CommonToolsMCP: web_search returned {sum(len(b['results']) for b in bundles)} hits "
            f"across {len(bundles)} queries"
        )
        return format_results(bundles)

    return mcp
