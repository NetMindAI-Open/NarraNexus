"""
@file_name: web_search_brave_tool.py
@author: Bin Liang
@date: 2026-04-21
@description: Brave Search API backed web_search MCP tool (cloud edition).

Registered on the CommonToolsModule MCP server when BRAVE_API_KEY is
set. Uses native-async httpx.AsyncClient — cancellation works at the
asyncio layer, no subprocess isolation needed (contrast with
web_search_ddgs_tool which wraps a sync library).

Public entry point: ``register(mcp, api_key)`` — adds the
@mcp.tool() web_search handler to the given FastMCP instance. Call
exactly once per MCP server.

Defense in depth (see spec §7):
  1. httpx.Timeout on every HTTP call
  2. asyncio.wait_for per-query (_PER_QUERY_TIMEOUT_S)
  3. asyncio.wait_for on gather (_OVERALL_TIMEOUT_S)
  4. Retry loop K=3 with linear backoff (429 / 5xx only)
  5. with_mcp_timeout outer handler cap (_HANDLER_TIMEOUT_S)
"""

import asyncio
from typing import Any

import httpx
from loguru import logger
from mcp.server.fastmcp import FastMCP

from .._common_tools_mcp_tools import with_mcp_timeout
from .web_search import format_results


_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

_HTTPX_TIMEOUT = httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=3.0)
_PER_QUERY_TIMEOUT_S: float = 10.0
_OVERALL_TIMEOUT_S: float = 25.0
_HANDLER_TIMEOUT_S: float = 45.0

_MAX_ATTEMPTS: int = 3
_RETRY_BACKOFF_S: float = 0.5


class _BraveRateLimited(Exception):
    """429 from Brave — retry-eligible at the outer loop."""


class _BraveServerError(Exception):
    """5xx from Brave — retry-eligible at the outer loop."""


async def _fetch_one(
    client: httpx.AsyncClient, query: str, max_results: int, api_key: str
) -> dict[str, Any]:
    """Fetch one query from Brave. Never raises for non-retryable errors —
    those land as ``bundle['error']``. Retry-eligible failures (429, 5xx)
    raise ``_BraveRateLimited`` / ``_BraveServerError`` which the outer
    retry loop catches.
    """
    try:
        resp = await asyncio.wait_for(
            client.get(
                _ENDPOINT,
                params={"q": query, "count": min(max_results, 20)},
                headers={
                    "X-Subscription-Token": api_key,
                    "Accept": "application/json",
                },
            ),
            timeout=_PER_QUERY_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.warning(f"brave query timed out after {_PER_QUERY_TIMEOUT_S}s: {query!r}")
        return {
            "query": query,
            "error": f"timed out after {_PER_QUERY_TIMEOUT_S}s",
            "results": [],
        }
    except httpx.HTTPError as e:
        logger.warning(f"brave query http error: {query!r} → {e}")
        return {"query": query, "error": f"http error: {e}", "results": []}

    if resp.status_code == 401:
        return {
            "query": query,
            "error": "brave auth rejected (check BRAVE_API_KEY)",
            "results": [],
        }
    if resp.status_code == 429:
        raise _BraveRateLimited(f"rate limited on query {query!r}")
    if resp.status_code >= 500:
        raise _BraveServerError(f"brave {resp.status_code} on {query!r}")
    if resp.status_code != 200:
        return {
            "query": query,
            "error": f"brave {resp.status_code}: {resp.text[:200]}",
            "results": [],
        }

    data = resp.json()
    web_results = (data.get("web", {}) or {}).get("results", []) or []
    return {
        "query": query,
        "error": None,
        "results": [
            {
                "title": r.get("title") or "",
                "url": r.get("url") or "",
                "snippet": r.get("description") or "",
            }
            for r in web_results[:max_results]
        ],
    }


async def _search_many_brave(
    queries: list[str], max_results: int, api_key: str
) -> list[dict[str, Any]]:
    """Fan queries out in parallel; return per-query bundles."""
    cleaned = [q.strip() for q in queries if q and q.strip()]
    if not cleaned:
        return []

    async with httpx.AsyncClient(timeout=_HTTPX_TIMEOUT) as client:
        try:
            bundles = await asyncio.wait_for(
                asyncio.gather(
                    *(_fetch_one(client, q, max_results, api_key) for q in cleaned)
                ),
                timeout=_OVERALL_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.exception(
                f"brave _search_many overall timeout after {_OVERALL_TIMEOUT_S}s "
                f"(per-query wrapper should have caught this — investigate)"
            )
            bundles = [
                {
                    "query": q,
                    "error": f"overall timeout after {_OVERALL_TIMEOUT_S}s",
                    "results": [],
                }
                for q in cleaned
            ]
    return bundles


async def _search_with_retry(
    queries: list[str], max_results: int, api_key: str
) -> list[dict[str, Any]]:
    """Retry on transient failures (429, 5xx). Client errors surface in bundles."""
    last_error: str = ""
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return await _search_many_brave(queries, max_results, api_key)
        except (_BraveRateLimited, _BraveServerError) as e:
            last_error = str(e)
            logger.warning(
                f"brave attempt {attempt}/{_MAX_ATTEMPTS} failed: {e}"
            )
        if attempt < _MAX_ATTEMPTS:
            await asyncio.sleep(_RETRY_BACKOFF_S * attempt)
    raise RuntimeError(
        f"brave search failed after {_MAX_ATTEMPTS} attempts: {last_error}"
    )


def register(mcp: FastMCP, api_key: str) -> None:
    """Register the Brave-backed web_search tool on the given MCP server."""

    @mcp.tool()
    @with_mcp_timeout(_HANDLER_TIMEOUT_S)
    async def web_search(
        queries: list[str],
        max_results_per_query: int = 5,
    ) -> str:
        """Search the web via Brave Search and return the top hits.

        Backed by Brave's independent search index (not Google/Bing).
        Strong on mainstream topics and fast-moving news. Coverage can
        be thinner than Google for very long-tail or specialized queries.

        Accepts a **list** of queries and runs them in parallel.

        Each entry in `queries` can be EITHER:
        - A natural-language question (e.g. "What changed in React 20?")
        - A set of keywords (e.g. "react 20 release notes")

        Args:
            queries: List of search queries. Empty strings are dropped.
                Recommended: 1–5 queries per call.
            max_results_per_query: Max hits per query. Default 5, hard cap 20.

        Returns:
            Markdown-formatted results grouped by query. Each hit has title,
            URL, and a short snippet. If a query fails, the error is reported
            inline without breaking the other queries.
        """
        try:
            bundles = await _search_with_retry(queries, max_results_per_query, api_key)
        except RuntimeError as e:
            logger.exception(f"CommonToolsMCP: brave web_search gave up: {e}")
            return f"web_search failed: {e}"

        logger.info(
            f"CommonToolsMCP: brave web_search returned "
            f"{sum(len(b['results']) for b in bundles)} hits "
            f"across {len(bundles)} queries"
        )
        return format_results(bundles)
