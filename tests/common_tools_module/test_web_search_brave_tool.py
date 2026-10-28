"""
@file_name: test_web_search_brave_tool.py
@author: Bin Liang
@date: 2026-04-21
@description: Brave-backed web_search tool contract tests.

Mocks httpx.AsyncClient so tests never hit the real Brave endpoint.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import httpx
import pytest

from narranexus_plugins.common_tools_module._common_tools_impl import (
    web_search_brave_tool as brave,
)


def _make_response(status_code: int, json_data: dict | None = None, text: str = "") -> MagicMock:
    """Build a minimal httpx.Response-like mock."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_data or {})
    resp.text = text
    return resp


def _patch_async_client(monkeypatch, handler):
    """Patch httpx.AsyncClient so each .get() call is routed to `handler(url, params, headers)`.

    handler must be an async callable returning a Response-like mock.
    """
    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, url, params=None, headers=None):
            return await handler(url, params, headers)

    monkeypatch.setattr(brave.httpx, "AsyncClient", _FakeClient)


# -------- happy path -------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_single_query_returns_formatted_results(monkeypatch):
    """One query → Brave returns 2 results → bundle shape matches DDGS output."""
    async def handler(url, params, headers):
        assert url == brave._ENDPOINT
        assert params["q"] == "asyncio cancel"
        assert headers["X-Subscription-Token"] == "test-key"
        return _make_response(200, {
            "web": {
                "results": [
                    {
                        "title": "Asyncio Docs",
                        "url": "https://docs.python.org/3/library/asyncio.html",
                        "description": "asyncio library reference",
                    },
                    {
                        "title": "Real Python: asyncio",
                        "url": "https://realpython.com/async-io/",
                        "description": "Tutorial on async",
                    },
                ]
            }
        })

    _patch_async_client(monkeypatch, handler)
    bundles = await brave._search_with_retry(["asyncio cancel"], 5, "test-key")

    assert len(bundles) == 1
    b = bundles[0]
    assert b["query"] == "asyncio cancel"
    assert b["error"] is None
    assert len(b["results"]) == 2
    assert b["results"][0] == {
        "title": "Asyncio Docs",
        "url": "https://docs.python.org/3/library/asyncio.html",
        "snippet": "asyncio library reference",
    }


@pytest.mark.asyncio
async def test_multiple_queries_run_in_parallel(monkeypatch):
    """N queries are in flight AT THE SAME TIME, not one after another.

    Asserted via peak concurrent occupancy, NOT wall-clock elapsed time.

    The wall-clock version of this test ("3 x 200ms sequential = 600ms, so
    assert elapsed < 0.5") could not tell "the code became sequential" from
    "the runner is slow", and on 2026-08-19 CI it started reporting 0.79s for
    a dispatch that is provably parallel (same test, same commit, 0.30s
    locally; it failed on dev at 568eb5fd with none of the PR's changes).
    A test that fails when the machine is busy trains people to re-run it,
    which is how a real regression gets waved through.

    Peak occupancy is a structural property of the dispatch: with
    asyncio.gather every handler is entered before any of them resumes from
    its await, so peak == N. If the implementation ever awaits each request in
    turn, peak == 1 on the fastest machine in the world and on the slowest.
    Timing is still present (the sleep keeps handlers overlapping) but nothing
    is asserted about it.
    """
    in_flight = 0
    peak = 0

    async def handler(url, params, headers):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        # Long enough that every sibling is dispatched before the first one
        # resumes; the event loop drains ready callbacks before timer ones.
        await asyncio.sleep(0.2)
        in_flight -= 1
        return _make_response(200, {
            "web": {"results": [
                {"title": f"t-{params['q']}", "url": "https://x/", "description": "s"}
            ]}
        })

    _patch_async_client(monkeypatch, handler)
    bundles = await brave._search_with_retry(["q1", "q2", "q3"], 5, "test-key")

    assert peak == 3, f"queries ran sequentially (peak concurrency {peak}, want 3)"
    assert len(bundles) == 3
    assert {b["query"] for b in bundles} == {"q1", "q2", "q3"}


@pytest.mark.asyncio
async def test_empty_queries_returns_empty_no_http_call(monkeypatch):
    """`web_search([])` short-circuits without hitting the network."""
    called = False

    async def handler(url, params, headers):
        nonlocal called
        called = True
        return _make_response(200, {"web": {"results": []}})

    _patch_async_client(monkeypatch, handler)
    bundles = await brave._search_with_retry([], 5, "test-key")
    assert bundles == []
    assert not called


@pytest.mark.asyncio
async def test_max_results_respected(monkeypatch):
    """The `count` query param is clamped to min(max_results, 20)."""
    captured_params: dict = {}

    async def handler(url, params, headers):
        captured_params.update(params)
        return _make_response(200, {"web": {"results": []}})

    _patch_async_client(monkeypatch, handler)
    await brave._search_with_retry(["x"], max_results=30, api_key="test-key")
    assert captured_params["count"] == 20  # clamped to max 20

    await brave._search_with_retry(["x"], max_results=3, api_key="test-key")
    assert captured_params["count"] == 3


# -------- the ladder itself ---------------------------------------------


def test_the_brave_timeout_ladder_keeps_its_order_and_floors():
    """The shipped numbers need somewhere to be checked.

    The two timeout tests below scale these constants down to 0.2s / 0.3s to run
    fast — correct, and the same trick `test_web_search_timeouts.py` uses — but
    it means nothing in the suite had ever asserted the values that actually
    ship. Order matters (a per-query cap above the overall cap is dead code, and
    the incident behind this three-layer defense was every layer delegating its
    timeout to the next one down); floors matter too, because a ladder scaled to
    milliseconds in production would turn a slow network into a fast failure.
    """
    assert 0 < brave._PER_QUERY_TIMEOUT_S < brave._OVERALL_TIMEOUT_S
    assert brave._PER_QUERY_TIMEOUT_S >= 5
    assert brave._OVERALL_TIMEOUT_S >= 15


# -------- per-query timeout ---------------------------------------------


@pytest.mark.asyncio
async def test_per_query_timeout_returns_error_bundle(monkeypatch):
    """Slow Brave response → per-query wait_for fires → bundle error, no raise."""
    monkeypatch.setattr(brave, "_PER_QUERY_TIMEOUT_S", 0.2)

    async def handler(url, params, headers):
        await asyncio.sleep(5.0)  # much longer than per-query cap
        return _make_response(200, {"web": {"results": []}})

    _patch_async_client(monkeypatch, handler)

    start = time.monotonic()
    bundles = await brave._search_with_retry(["slow-q"], 5, "test-key")
    elapsed = time.monotonic() - start

    assert elapsed < 2.0, f"per-query timeout didn't fire ({elapsed:.2f}s)"
    assert len(bundles) == 1
    assert bundles[0]["query"] == "slow-q"
    assert bundles[0]["results"] == []
    assert "timed out" in bundles[0]["error"]


# -------- overall timeout -----------------------------------------------


@pytest.mark.asyncio
async def test_overall_timeout_fires_if_per_query_misses(monkeypatch):
    """Defense-in-depth: if per-query somehow fails to fire, overall catches it.

    We patch _fetch_one to just sleep forever (bypassing per-query's own wait_for).
    """
    monkeypatch.setattr(brave, "_OVERALL_TIMEOUT_S", 0.3)

    async def never_finishes(client, query, max_results, api_key):
        await asyncio.sleep(30)
        return {"query": query, "error": None, "results": []}

    monkeypatch.setattr(brave, "_fetch_one", never_finishes)

    start = time.monotonic()
    bundles = await brave._search_with_retry(["q1", "q2"], 5, "test-key")
    elapsed = time.monotonic() - start

    assert elapsed < 2.0, f"overall timeout didn't fire ({elapsed:.2f}s)"
    assert len(bundles) == 2
    for b in bundles:
        assert "overall timeout" in b["error"]


# -------- retry on 429 ---------------------------------------------------


@pytest.mark.asyncio
async def test_retry_on_429_succeeds_on_next_attempt(monkeypatch):
    """First attempt 429 → retry succeeds with 200."""
    monkeypatch.setattr(brave, "_RETRY_BACKOFF_S", 0.01)
    call_count = 0

    async def handler(url, params, headers):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _make_response(429, text="rate limited")
        return _make_response(200, {
            "web": {"results": [{"title": "ok", "url": "https://x/", "description": "s"}]}
        })

    _patch_async_client(monkeypatch, handler)
    bundles = await brave._search_with_retry(["q"], 5, "test-key")
    assert len(bundles) == 1
    assert bundles[0]["error"] is None
    assert bundles[0]["results"][0]["title"] == "ok"
    assert call_count == 2


# -------- retry on 5xx ---------------------------------------------------


@pytest.mark.asyncio
async def test_retry_on_5xx_succeeds_after_flake(monkeypatch):
    """First 503 → retry succeeds."""
    monkeypatch.setattr(brave, "_RETRY_BACKOFF_S", 0.01)
    call_count = 0

    async def handler(url, params, headers):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _make_response(503, text="unavailable")
        return _make_response(200, {"web": {"results": []}})

    _patch_async_client(monkeypatch, handler)
    bundles = await brave._search_with_retry(["q"], 5, "test-key")
    assert bundles[0]["error"] is None
    assert call_count == 2


# -------- retry exhaustion -----------------------------------------------


@pytest.mark.asyncio
async def test_retry_exhausted_raises_runtime_error(monkeypatch):
    """All K=3 attempts 500 → RuntimeError, no hang."""
    monkeypatch.setattr(brave, "_RETRY_BACKOFF_S", 0.01)
    monkeypatch.setattr(brave, "_MAX_ATTEMPTS", 3)

    async def handler(url, params, headers):
        return _make_response(500, text="server error")

    _patch_async_client(monkeypatch, handler)

    with pytest.raises(RuntimeError, match="failed after 3 attempts"):
        await brave._search_with_retry(["q"], 5, "test-key")


# -------- 401 does NOT retry --------------------------------------------


@pytest.mark.asyncio
async def test_401_auth_failure_surfaces_in_bundle_not_retried(monkeypatch):
    """401 is unrecoverable — bundle error, not retry."""
    call_count = 0

    async def handler(url, params, headers):
        nonlocal call_count
        call_count += 1
        return _make_response(401, text="invalid key")

    _patch_async_client(monkeypatch, handler)
    bundles = await brave._search_with_retry(["q"], 5, "test-key")

    assert len(bundles) == 1
    assert "auth rejected" in bundles[0]["error"]
    assert call_count == 1, f"401 was retried {call_count} times; must not retry"


# -------- MCP handler outer timeout -------------------------------------


@pytest.mark.asyncio
async def test_mcp_handler_outer_timeout_fires_even_if_retry_hangs(monkeypatch):
    """If _search_with_retry somehow hangs, with_mcp_timeout catches it."""
    from mcp.server.fastmcp import FastMCP
    monkeypatch.setattr(brave, "_HANDLER_TIMEOUT_S", 1.0)

    async def hang_forever(queries, max_results, api_key):
        await asyncio.sleep(60)
        return []

    monkeypatch.setattr(brave, "_search_with_retry", hang_forever)

    mcp = FastMCP("test")
    brave.register(mcp, api_key="test-key")

    start = time.monotonic()
    result = await mcp.call_tool(
        "web_search", {"queries": ["x"], "max_results_per_query": 3}
    )
    elapsed = time.monotonic() - start

    assert elapsed < 3.0, f"outer handler took {elapsed:.2f}s with 1s cap"
    assert result is not None


@pytest.mark.asyncio
async def test_mcp_handler_returns_error_string_when_retry_exhausted(monkeypatch):
    """All retries fail -> handler returns 'web_search failed: ...' string."""
    from mcp.server.fastmcp import FastMCP

    async def always_raise(queries, max_results, api_key):
        raise RuntimeError("brave search failed after 3 attempts: ...")

    monkeypatch.setattr(brave, "_search_with_retry", always_raise)

    mcp = FastMCP("test")
    brave.register(mcp, api_key="test-key")
    result = await mcp.call_tool(
        "web_search", {"queries": ["x"], "max_results_per_query": 3}
    )
    as_text = str(result)
    assert "web_search failed" in as_text


@pytest.mark.asyncio
async def test_mcp_handler_formats_bundles_as_markdown_on_success(monkeypatch):
    """Happy path through the MCP tool: bundles -> format_results markdown."""
    from mcp.server.fastmcp import FastMCP
    fake_bundles = [
        {
            "query": "react",
            "error": None,
            "results": [
                {"title": "React Docs", "url": "https://react.dev/", "snippet": "UI library"}
            ],
        }
    ]

    async def spawn_success(queries, max_results, api_key):
        return fake_bundles

    monkeypatch.setattr(brave, "_search_with_retry", spawn_success)

    mcp = FastMCP("test")
    brave.register(mcp, api_key="test-key")
    result = await mcp.call_tool(
        "web_search", {"queries": ["react"], "max_results_per_query": 3}
    )
    as_text = str(result)
    assert "Query 1: react" in as_text
    assert "React Docs" in as_text
    assert "https://react.dev/" in as_text
