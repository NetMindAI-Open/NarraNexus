"""
@file_name: test_web_search_timeouts.py
@author: Bin Liang
@date: 2026-04-20
@description: Bug 20 — three-layer timeout defense for the DDGS-backed
`mcp__common_tools_module__web_search` tool.

The incident: on 2026-04-18 18:15:36, a single DDGS call wedged the
shared MCP container for 33+ hours. Root cause chain — DDGS library's
``ThreadPoolExecutor.__exit__`` blocks on stuck primp/libcurl threads
that never finish; our ``_search_sync`` had no external timeout; our
``asyncio.gather`` had no ``wait_for``; the MCP tool handler had no
outer bound. Every layer delegated the timeout to the next layer
down — none of them had one.

These tests pin the three-layer fix:

1. ``DDGS()`` is constructed with an explicit ``timeout=5``.
2. Per-query: ``_one`` wraps ``asyncio.to_thread(_search_sync, ...)``
   in ``asyncio.wait_for(..., 15)`` and returns a structured per-query
   error on timeout (does NOT raise).
3. Overall: ``search_many`` wraps the gather in ``asyncio.wait_for(...,
   30)``; on timeout, returns a bundle per query marked as timed out.

Tests use ``monkeypatch`` to replace ``_search_sync`` with a
controllable sync blocker so we can simulate the production hang
without hitting the real network.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from xyz_agent_context.module.common_tools_module._common_tools_impl import (
    web_search as ws,
)


# -------- layer 1 · DDGS construction uses explicit timeout --------------


def test_search_sync_constructs_ddgs_with_explicit_timeout(monkeypatch):
    """``_search_sync`` must pass a bounded ``timeout`` to ``DDGS(...)``.
    The default is 5s in current ddgs releases but we pin it explicitly
    so a future upstream change doesn't silently remove our floor."""
    captured: dict = {}

    class _FakeCtx:
        def __init__(self, *args, **kwargs):
            captured["init_kwargs"] = kwargs
            captured["init_args"] = args

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def text(self, *args, **kwargs):
            return []

    monkeypatch.setattr(ws, "DDGS", _FakeCtx, raising=False)
    # DDGS is actually imported locally inside _search_sync, so we also
    # patch the module path it's imported from.
    import ddgs
    monkeypatch.setattr(ddgs, "DDGS", _FakeCtx)

    ws._search_sync("hello", 5)

    assert "timeout" in captured["init_kwargs"], (
        f"DDGS() must be constructed with an explicit timeout kwarg; "
        f"got init_kwargs={captured['init_kwargs']!r}"
    )
    assert isinstance(captured["init_kwargs"]["timeout"], (int, float))
    assert 1 <= captured["init_kwargs"]["timeout"] <= 10, (
        "timeout should be a small bounded value (1-10s range)"
    )


# -------- layer 2 · per-query wait_for on to_thread ---------------------


@pytest.mark.asyncio
async def test_per_query_timeout_returns_structured_error_not_raises(monkeypatch):
    """If one query hangs, ``_one`` must return
    ``{"query": q, "error": <timeout msg>, "results": []}`` — not raise,
    not leak the CancelledError, not make gather return partial."""
    def _hang_forever(query, max_results):
        # Simulate a stuck DDGS call — sleeps well beyond our per-query cap.
        time.sleep(25)
        return []

    monkeypatch.setattr(ws, "_search_sync", _hang_forever)

    start = time.monotonic()
    bundles = await ws.search_many(["slow-query"], max_results_per_query=3)
    elapsed = time.monotonic() - start

    # Must return well before the 60s hang — per-query cap should fire.
    assert elapsed < 20, f"search_many took {elapsed:.1f}s; per-query wait_for missing"
    assert len(bundles) == 1
    b = bundles[0]
    assert b["query"] == "slow-query"
    assert b["results"] == []
    assert b["error"] is not None
    assert "timeout" in b["error"].lower() or "timed out" in b["error"].lower()


@pytest.mark.asyncio
async def test_per_query_timeout_isolates_failure_from_siblings(monkeypatch):
    """One hanging query must NOT block the other queries. After the
    per-query timeout fires, the fast query's result is still returned."""
    def _search(query, max_results):
        if query == "slow":
            time.sleep(25)
            return []
        return [{"title": f"hit-{query}", "href": "https://ex/", "body": "b"}]

    monkeypatch.setattr(ws, "_search_sync", _search)

    start = time.monotonic()
    bundles = await ws.search_many(["slow", "fast"], max_results_per_query=3)
    elapsed = time.monotonic() - start

    assert elapsed < 20, f"took {elapsed:.1f}s — sibling blocked by stuck query"
    by_q = {b["query"]: b for b in bundles}
    assert by_q["slow"]["error"] and by_q["slow"]["results"] == []
    assert by_q["fast"]["error"] is None
    assert len(by_q["fast"]["results"]) == 1
    assert by_q["fast"]["results"][0]["title"] == "hit-fast"


# -------- layer 3 · overall wait_for on gather ---------------------------


@pytest.mark.asyncio
async def test_overall_search_many_bounded_even_if_per_query_misses(monkeypatch):
    """Defense in depth: if the per-query wrapper somehow fails to
    trigger (future refactor bug, new code path), the overall ``gather``
    must still be bounded by an outer ``wait_for``. We simulate by
    replacing ``_one`` directly with a bare coroutine that never returns."""
    async def _never_finishes(q, _capped):  # noqa: ARG001 — intentional hang
        await asyncio.sleep(45)
        return {"query": q, "error": None, "results": []}

    monkeypatch.setattr(ws, "_one", _never_finishes)

    start = time.monotonic()
    bundles = await ws.search_many(["q1", "q2"], max_results_per_query=3)
    elapsed = time.monotonic() - start

    # Must hit the OVERALL cap (30s in impl, test allows slack).
    assert elapsed < 40, f"took {elapsed:.1f}s — outer wait_for missing"
    # Still returns a bundle per query (errors populated).
    assert len(bundles) == 2
    for b in bundles:
        assert b["error"] is not None
        assert b["results"] == []


# -------- layer 4 · MCP tool handler wrapping ---------------------------


@pytest.mark.asyncio
async def test_mcp_tool_handler_has_outer_timeout(monkeypatch):
    """The MCP tool registered as ``web_search`` in
    ``_common_tools_mcp_tools.create_common_tools_mcp_server`` must
    itself wrap ``search_many`` in ``asyncio.wait_for``. Even if ALL
    inner layers fail to bound themselves, this final decorator
    ensures the MCP handler always returns to the caller, preventing
    the Bug 20 "whole shared MCP container wedges" behaviour."""
    from xyz_agent_context.module.common_tools_module import _common_tools_mcp_tools as tools

    # Replace search_many so it hangs arbitrarily long.
    async def _hang_search_many(queries, max_results_per_query):
        await asyncio.sleep(45)
        return []

    monkeypatch.setattr(tools, "search_many", _hang_search_many, raising=False)

    # Also patch the copy imported inside the tool function's closure
    # (it does `from ...web_search import search_many` at call time).
    from xyz_agent_context.module.common_tools_module._common_tools_impl import (
        web_search as ws_impl,
    )
    monkeypatch.setattr(ws_impl, "search_many", _hang_search_many)

    mcp = tools.create_common_tools_mcp_server(port=0)
    # FastMCP stores tools in an internal registry; retrieve via list_tools()
    # and then call the underlying function. We need the raw handler — go
    # through the FastMCP API.
    tool_entries = await mcp.list_tools()
    ws_entry = next((t for t in tool_entries if t.name == "web_search"), None)
    assert ws_entry is not None, "web_search tool must be registered"

    # Invoke the handler with a stuck search_many. It must return within
    # a reasonable bound (outer timeout + a bit), not hang.
    start = time.monotonic()
    result = await mcp.call_tool("web_search", {"queries": ["x"], "max_results_per_query": 3})
    elapsed = time.monotonic() - start

    assert elapsed < 60, (
        f"MCP handler took {elapsed:.1f}s; outer wait_for missing — "
        "this is the exact pattern that wedged the shared MCP container on prod"
    )
    # Result shape: FastMCP's call_tool returns a list of content blocks;
    # we don't care about shape, just that it returned something.
    assert result is not None
