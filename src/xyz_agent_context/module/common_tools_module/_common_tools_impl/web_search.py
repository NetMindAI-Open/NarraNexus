"""
@file_name: web_search.py
@author: Bin Liang
@date: 2026-04-17
@description: DuckDuckGo-backed web search implementation

Runs a list of queries through the `ddgs` library in parallel worker threads
(DDGS is sync, so we wrap each call in asyncio.to_thread to avoid blocking
the event loop).

Isolation:
- No per-agent state. Pure function over a query list.
- One DDGS() context per query — the library is cheap to instantiate and
  individual sessions keep cookies isolated, reducing rate-limit collateral.

Timeout layering (Bug 20, 2026-04-20)
-------------------------------------
The 2026-04-18 incident: a single DDGS call wedged the shared MCP
container for 33+ hours. Root cause — primp/libcurl (DDGS's HTTP
backend) got into a ``CLOSE_WAIT`` state it never cleaned up; the
stuck thread blocked DDGS's internal ``ThreadPoolExecutor.shutdown``;
our outer code had no bound of its own. Fix is defense in depth at
three layers, any one of which can rescue the call:

1. DDGS(``timeout=DDGS_CLIENT_TIMEOUT_S``) — explicit, pinned so an
   upstream default change can't silently break us.
2. Per-query ``asyncio.wait_for`` around ``asyncio.to_thread(_search_sync)``.
   If DDGS's own timeout doesn't fire (e.g. stuck in shutdown wait),
   we still bail at ``PER_QUERY_TIMEOUT_S``.
3. Overall ``asyncio.wait_for`` around ``asyncio.gather(...)``. If the
   per-query layer somehow misses (future refactor bug), the overall
   ``OVERALL_TIMEOUT_S`` guarantees ``search_many`` returns.

Known residual: Python threads can't be externally cancelled, so a
DDGS call stuck at the C/Rust layer will leak its worker thread even
though our asyncio layer has moved on. The default asyncio thread
pool (32+ workers) tolerates a handful of leaks. Full subprocess
isolation is a future upgrade — tracked in
``reference/self_notebook/todo/waiting/web_search_subprocess_isolation.md``.
"""

import asyncio
from typing import Any

from loguru import logger

MAX_RESULTS_CAP = 10
DEFAULT_REGION = "wt-wt"  # worldwide, any language
DEFAULT_SAFESEARCH = "moderate"

# Three-layer timeout budget. Numbers chosen so each outer layer gives
# the inner layer a chance to finish normally plus a small cushion:
#   DDGS_CLIENT_TIMEOUT_S (5) < PER_QUERY_TIMEOUT_S (15) < OVERALL_TIMEOUT_S (30)
DDGS_CLIENT_TIMEOUT_S = 5
PER_QUERY_TIMEOUT_S = 15.0
OVERALL_TIMEOUT_S = 30.0


def _search_sync(query: str, max_results: int) -> list[dict[str, Any]]:
    """Blocking DuckDuckGo text search. Run this under asyncio.to_thread."""
    from ddgs import DDGS

    with DDGS(timeout=DDGS_CLIENT_TIMEOUT_S) as ddgs:
        raw = ddgs.text(
            query=query,
            region=DEFAULT_REGION,
            safesearch=DEFAULT_SAFESEARCH,
            max_results=max_results,
        )
        return list(raw or [])


async def _one(q: str, capped: int) -> dict[str, Any]:
    """Run one query with a per-query hard cap.

    Returns a bundle even on failure — a stuck query becomes
    ``{"error": "...timed out...", "results": []}`` for its slot,
    leaving sibling queries unaffected.
    """
    try:
        raw = await asyncio.wait_for(
            asyncio.to_thread(_search_sync, q, capped),
            timeout=PER_QUERY_TIMEOUT_S,
        )
        normalized = [
            {
                "title": (r.get("title") or "").strip(),
                "url": (r.get("href") or r.get("url") or "").strip(),
                "snippet": (r.get("body") or r.get("snippet") or "").strip(),
            }
            for r in raw
        ]
        return {"query": q, "error": None, "results": normalized}
    except asyncio.TimeoutError:
        logger.warning(
            f"web_search query timed out after {PER_QUERY_TIMEOUT_S}s: {q!r}"
        )
        return {
            "query": q,
            "error": f"search timed out after {PER_QUERY_TIMEOUT_S}s",
            "results": [],
        }
    except Exception as e:  # noqa: BLE001 — surface to caller, don't crash
        logger.warning(f"web_search query failed: {q!r} → {e}")
        return {"query": q, "error": str(e), "results": []}


async def search_many(queries: list[str], max_results_per_query: int) -> list[dict[str, Any]]:
    """Fan queries out in parallel, return per-query result bundles.

    Each bundle:
        {"query": str, "error": str | None, "results": [{"title", "url", "snippet"}...]}

    Never raises — errors are reported per query so one dead query doesn't
    take down the rest. Timeouts materialise as per-query errors (see
    ``_one``) or, as a last-line defense, as a blanket "overall timeout"
    bundle per query if even ``gather`` misses its bound.
    """
    capped = max(1, min(int(max_results_per_query), MAX_RESULTS_CAP))
    cleaned = [q.strip() for q in queries if q and q.strip()]
    if not cleaned:
        return []

    try:
        return await asyncio.wait_for(
            asyncio.gather(*(_one(q, capped) for q in cleaned)),
            timeout=OVERALL_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.error(
            f"web_search overall timeout fired after {OVERALL_TIMEOUT_S}s "
            f"(per-query wrapper should have caught this earlier — investigate)"
        )
        return [
            {
                "query": q,
                "error": f"overall search timed out after {OVERALL_TIMEOUT_S}s",
                "results": [],
            }
            for q in cleaned
        ]


def format_results(bundles: list[dict[str, Any]]) -> str:
    """Render search bundles into a compact markdown block for the LLM."""
    if not bundles:
        return "No queries provided."

    lines: list[str] = []
    for idx, bundle in enumerate(bundles, start=1):
        lines.append(f"### Query {idx}: {bundle['query']}")
        if bundle["error"]:
            lines.append(f"_search error: {bundle['error']}_")
            lines.append("")
            continue
        if not bundle["results"]:
            lines.append("_no results_")
            lines.append("")
            continue
        for i, hit in enumerate(bundle["results"], start=1):
            title = hit["title"] or "(untitled)"
            url = hit["url"] or "(no url)"
            snippet = hit["snippet"] or "(no snippet)"
            lines.append(f"{i}. **{title}**")
            lines.append(f"   {url}")
            lines.append(f"   {snippet}")
        lines.append("")
    return "\n".join(lines).rstrip()
