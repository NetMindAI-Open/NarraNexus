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

from narranexus_plugins.common_tools_module._common_tools_impl import (
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


# -------- the ladder itself ---------------------------------------------


def test_the_timeout_ladder_keeps_its_order():
    """Each layer must fire before the one above it, or the inner bound is
    dead code and the incident's "every layer delegated downwards" shape is
    back with extra steps.

    Pinned here because the behavioural tests below scale these constants down
    to run in a second — the shipped numbers need somewhere to be checked.
    Ordering alone would be satisfied by 0.001 < 0.002 < 0.003, so the floors
    are asserted too: a DDGS call has to get a realistic chance to answer
    before we call it stuck, or the three-layer defense becomes a three-layer
    way to fail fast on a slow network.
    """
    assert 0 < ws.DDGS_CLIENT_TIMEOUT_S < ws.PER_QUERY_TIMEOUT_S < ws.OVERALL_TIMEOUT_S
    assert ws.DDGS_CLIENT_TIMEOUT_S >= 3
    assert ws.PER_QUERY_TIMEOUT_S >= 10
    assert ws.OVERALL_TIMEOUT_S >= 20


# -------- layer 2 · per-query wait_for on to_thread ---------------------
#
# These three used to wait out the real ladder: 15s and 30s of genuine
# sleeping, plus the to_thread blockers still running at loop teardown —
# 80 of the suite's 178 seconds, for timeouts whose only interesting property
# is that they fire. Scaled down the same way the Brave twin next door already
# does it (`test_web_search_brave_tool.py`); the ladder's real values are
# pinned above.
#
# The blockers still outlive the test — `asyncio.run` joins the default executor
# at loop close — but they now sleep 1.5s instead of 25s, so teardown went from
# ~10s to ~1.25s rather than to zero.

# Scaled 100x/60x/50x, not ~300x, on purpose. The assertions have to separate
# "the cap fired" from "the blocker finished" on a loaded CI runner, and a
# 50ms budget makes thread-pool startup jitter look like a missing timeout.
# A flaky guard is worse than a slow one.
# Inert in practice — DDGS_CLIENT_TIMEOUT_S is only read inside `_search_sync`,
# which all three tests below monkeypatch away. Kept so the fixture hands over a
# COMPLETE ladder: a future test that exercises the real `_search_sync` should
# not have to discover that one rung was missing.
_FAST_DDGS = 0.05
_FAST_PER_QUERY = 0.25
_FAST_OVERALL = 0.6

# Blockers overshoot the cap by 6x and the assertions sit halfway between:
# cap working lands at ~1x, cap missing lands at ~6x, and the verdict is
# never decided by a few milliseconds either way.
_OVERSHOOT = 6
_CEILING = 3


@pytest.fixture
def fast_ladder(monkeypatch):
    """The shipped ladder, three orders of magnitude smaller, order intact."""
    monkeypatch.setattr(ws, "DDGS_CLIENT_TIMEOUT_S", _FAST_DDGS)
    monkeypatch.setattr(ws, "PER_QUERY_TIMEOUT_S", _FAST_PER_QUERY)
    monkeypatch.setattr(ws, "OVERALL_TIMEOUT_S", _FAST_OVERALL)


@pytest.mark.asyncio
async def test_per_query_timeout_returns_structured_error_not_raises(
    monkeypatch, fast_ladder
):
    """If one query hangs, ``_one`` must return
    ``{"query": q, "error": <timeout msg>, "results": []}`` — not raise,
    not leak the CancelledError, not make gather return partial."""
    def _hang_forever(query, max_results):
        # Simulate a stuck DDGS call — sleeps past our per-query cap.
        time.sleep(_FAST_PER_QUERY * _OVERSHOOT)
        return []

    monkeypatch.setattr(ws, "_search_sync", _hang_forever)

    start = time.monotonic()
    bundles = await ws.search_many(["slow-query"], max_results_per_query=3)
    elapsed = time.monotonic() - start

    # Must return well before the blocker finishes — per-query cap should fire.
    assert elapsed < _FAST_PER_QUERY * _CEILING, (
        f"search_many took {elapsed:.2f}s; per-query wait_for missing"
    )
    assert len(bundles) == 1
    b = bundles[0]
    assert b["query"] == "slow-query"
    assert b["results"] == []
    assert b["error"] is not None
    assert "timeout" in b["error"].lower() or "timed out" in b["error"].lower()


@pytest.mark.asyncio
async def test_per_query_timeout_isolates_failure_from_siblings(
    monkeypatch, fast_ladder
):
    """One hanging query must NOT block the other queries. After the
    per-query timeout fires, the fast query's result is still returned."""
    def _search(query, max_results):
        if query == "slow":
            time.sleep(_FAST_PER_QUERY * _OVERSHOOT)
            return []
        return [{"title": f"hit-{query}", "href": "https://ex/", "body": "b"}]

    monkeypatch.setattr(ws, "_search_sync", _search)

    start = time.monotonic()
    bundles = await ws.search_many(["slow", "fast"], max_results_per_query=3)
    elapsed = time.monotonic() - start

    assert elapsed < _FAST_PER_QUERY * _CEILING, (
        f"took {elapsed:.2f}s — sibling blocked by stuck query"
    )
    by_q = {b["query"]: b for b in bundles}
    assert by_q["slow"]["error"] and by_q["slow"]["results"] == []
    assert by_q["fast"]["error"] is None
    assert len(by_q["fast"]["results"]) == 1
    assert by_q["fast"]["results"][0]["title"] == "hit-fast"


# -------- layer 3 · overall wait_for on gather ---------------------------


@pytest.mark.asyncio
async def test_overall_search_many_bounded_even_if_per_query_misses(
    monkeypatch, fast_ladder
):
    """Defense in depth: if the per-query wrapper somehow fails to
    trigger (future refactor bug, new code path), the overall ``gather``
    must still be bounded by an outer ``wait_for``. We simulate by
    replacing ``_one`` directly with a bare coroutine that never returns."""
    async def _never_finishes(q, _capped):  # noqa: ARG001 — intentional hang
        await asyncio.sleep(_FAST_OVERALL * _OVERSHOOT)
        return {"query": q, "error": None, "results": []}

    monkeypatch.setattr(ws, "_one", _never_finishes)

    start = time.monotonic()
    bundles = await ws.search_many(["q1", "q2"], max_results_per_query=3)
    elapsed = time.monotonic() - start

    # Must hit the OVERALL cap, not the blocker.
    assert elapsed < _FAST_OVERALL * _CEILING, (
        f"took {elapsed:.2f}s — outer wait_for missing"
    )
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
    itself wrap its coroutine in ``asyncio.wait_for``. Even if the
    subprocess layer + retry loop fail to bound themselves (shouldn't
    be possible, but defense in depth), this final decorator ensures
    the MCP handler always returns to the caller.

    Bug 24 refactor: the handler now calls ``_web_search_with_retry``
    (which spawns subprocesses) instead of ``search_many`` directly.
    We patch that single entry point to simulate a hang.
    """
    from narranexus_plugins.common_tools_module._common_tools_impl import (
        web_search_ddgs_tool as tools,
    )
    from narranexus_plugins.common_tools_module import _common_tools_mcp_tools as factory

    # Shrink the outer handler timeout so the test completes in a few
    # seconds instead of waiting out the production 110s cap. The
    # decorator reads this constant at create-server time, so monkeypatch
    # it BEFORE create_common_tools_mcp_server runs.
    monkeypatch.setattr(tools, "_WEB_SEARCH_HANDLER_TIMEOUT_S", 2.0)

    async def _hang_forever(queries, max_results):
        await asyncio.sleep(60)
        return []

    monkeypatch.setattr(tools, "_web_search_with_retry", _hang_forever)

    mcp = factory.create_common_tools_mcp_server()
    tool_entries = await mcp.list_tools()
    ws_entry = next((t for t in tool_entries if t.name == "web_search"), None)
    assert ws_entry is not None, "web_search tool must be registered"

    start = time.monotonic()
    result = await mcp.call_tool("web_search", {"queries": ["x"], "max_results_per_query": 3})
    elapsed = time.monotonic() - start

    # Handler timeout is 2s; allow a small margin for asyncio scheduling.
    assert elapsed < 5.0, (
        f"MCP handler took {elapsed:.1f}s with a 2s outer timeout — "
        "with_mcp_timeout is missing or misbehaving"
    )
    assert result is not None
