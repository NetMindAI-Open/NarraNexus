"""
@file_name: web_search_ddgs_tool.py
@author: Bin Liang
@date: 2026-04-21
@description: DDGS-backed web_search MCP tool (local / no-API-key edition).

Registered on the CommonToolsModule MCP server when BRAVE_API_KEY is
absent from the environment. The implementation delegates to a
dedicated python subprocess (web_search_runner) for OS-level resource
isolation — see the header comment of web_search_runner.py for why.

Public entry point: ``register(mcp)`` — adds the @mcp.tool() web_search
handler to the given FastMCP instance. Call exactly once per MCP server.
"""

import asyncio
import json
import sys
from typing import Any

from loguru import logger
from mcp.server.fastmcp import FastMCP

from .._common_tools_mcp_tools import with_mcp_timeout


# =============================================================================
# Subprocess runner invocation constants (Bug 24 defense-in-depth)
# =============================================================================

_RUNNER_MODULE = (
    "xyz_agent_context.module.common_tools_module."
    "_common_tools_impl.web_search_runner"
)
_RUNNER_CMD: list[str] = [sys.executable, "-m", _RUNNER_MODULE]

_SUBPROCESS_TIMEOUT_S: float = 25.0
_MAX_ATTEMPTS: int = 4
_RETRY_BACKOFF_S: float = 1.0
_WEB_SEARCH_HANDLER_TIMEOUT_S: float = 110.0


class _RunnerFailure(Exception):
    """Raised when a single subprocess attempt fails in a retry-eligible way."""


async def _spawn_runner(queries: list[str], max_results: int) -> list[dict[str, Any]]:
    payload = json.dumps({
        "queries": queries,
        "max_results_per_query": max_results,
    }).encode("utf-8")

    proc = await asyncio.create_subprocess_exec(
        *_RUNNER_CMD,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=payload),
            timeout=_SUBPROCESS_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except (ProcessLookupError, OSError):
            pass
        raise

    if proc.returncode != 0:
        raise _RunnerFailure(
            f"runner exited with code {proc.returncode}; "
            f"stderr={stderr.decode('utf-8', errors='replace')[:500]!r}"
        )

    try:
        data = json.loads(stdout.decode("utf-8"))
        bundles = data["bundles"]
        if not isinstance(bundles, list):
            raise TypeError(f"bundles is not a list: {type(bundles).__name__}")
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise _RunnerFailure(
            f"runner stdout malformed: {e}; "
            f"got first 200 bytes={stdout[:200]!r}"
        ) from e

    return bundles


async def _web_search_with_retry(
    queries: list[str],
    max_results: int,
) -> list[dict[str, Any]]:
    last_error: str = ""
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return await _spawn_runner(queries, max_results)
        except asyncio.TimeoutError:
            last_error = f"subprocess timed out after {_SUBPROCESS_TIMEOUT_S}s (killed)"
            logger.warning(
                f"web_search attempt {attempt}/{_MAX_ATTEMPTS} "
                f"hit subprocess timeout; killed and will retry"
            )
        except _RunnerFailure as e:
            last_error = str(e)
            logger.warning(
                f"web_search attempt {attempt}/{_MAX_ATTEMPTS} failed: {e}"
            )

        if attempt < _MAX_ATTEMPTS:
            await asyncio.sleep(_RETRY_BACKOFF_S)

    raise RuntimeError(
        f"web_search failed after {_MAX_ATTEMPTS} attempts; last error: {last_error}"
    )


# =============================================================================
# Tool registration
# =============================================================================


def register(mcp: FastMCP) -> None:
    """Register the DDGS-backed web_search tool on the given MCP server."""

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
            format_results,
        )

        try:
            bundles = await _web_search_with_retry(queries, max_results_per_query)
        except RuntimeError as e:
            logger.exception(f"CommonToolsMCP: web_search gave up: {e}")
            return f"web_search failed: {e}"

        logger.info(
            f"CommonToolsMCP: web_search returned "
            f"{sum(len(b['results']) for b in bundles)} hits "
            f"across {len(bundles)} queries"
        )
        return format_results(bundles)
