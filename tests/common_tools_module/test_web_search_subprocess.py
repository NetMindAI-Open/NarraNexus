"""
@file_name: test_web_search_subprocess.py
@author: Bin Liang
@date: 2026-04-21
@description: Bug 24 — subprocess isolation + retry for web_search.

Bug 20 added asyncio timeouts that kept the handler responsive on
paper, but couldn't reclaim leaked threads / FDs from stuck DDGS
calls; enough leaks and the whole MCP process suffocates (FD table
exhaustion). Bug 24 moves DDGS into a fresh subprocess per call —
SIGKILL on timeout means OS-level resource reclamation is guaranteed.

These tests pin the subprocess + retry contract. We don't touch the
real DDG network; instead we monkeypatch ``_RUNNER_CMD`` to point at
``python -c <inline-script>`` that simulates each failure mode.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time

import pytest

from narranexus_plugins.common_tools_module._common_tools_impl import (
    web_search_ddgs_tool as tools,
)
from narranexus_plugins.common_tools_module import _common_tools_mcp_tools as factory


def _fake_runner_script(body: str) -> list[str]:
    """Build a ``python -c`` command that runs the given body as the runner."""
    return [sys.executable, "-c", body]


# -------- happy path ------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_subprocess_returns_bundles(monkeypatch):
    """Well-formed runner → bundles parsed and returned."""
    fake_bundles = [
        {
            "query": "q1",
            "error": None,
            "results": [{"title": "T", "url": "https://ex/", "snippet": "S"}],
        }
    ]
    payload = json.dumps({"bundles": fake_bundles})
    script = (
        "import sys; sys.stdin.read(); "
        f"sys.stdout.write({payload!r})"
    )
    monkeypatch.setattr(tools, "_RUNNER_CMD", _fake_runner_script(script))

    bundles = await tools._web_search_with_retry(["q1"], 5)
    assert bundles == fake_bundles


@pytest.mark.asyncio
async def test_happy_path_receives_queries_on_stdin(monkeypatch):
    """Runner must receive the queries JSON on stdin.

    We have the fake runner echo the stdin back in the bundles so we can
    verify the wire contract the parent uses to pass queries.
    """
    # Echo the raw stdin payload back inside a "bundles" stub so parent
    # still parses successfully. Testing the actual transport.
    script = (
        "import sys, json; "
        "raw = sys.stdin.read(); "
        "sys.stdout.write(json.dumps({'bundles': [{'query': raw, "
        "'error': None, 'results': []}]}))"
    )
    monkeypatch.setattr(tools, "_RUNNER_CMD", _fake_runner_script(script))

    bundles = await tools._web_search_with_retry(["hello"], 3)
    assert len(bundles) == 1
    received = json.loads(bundles[0]["query"])
    assert received == {"queries": ["hello"], "max_results_per_query": 3}


# -------- timeout path ---------------------------------------------------


@pytest.mark.asyncio
async def test_subprocess_timeout_kills_and_raises(monkeypatch):
    """A subprocess that ignores stdin and sleeps forever must be killed
    at the per-attempt timeout, not allowed to drag the handler with it."""
    # Shorten timeout for fast test
    monkeypatch.setattr(tools, "_SUBPROCESS_TIMEOUT_S", 0.5)

    script = "import time; time.sleep(60)"
    monkeypatch.setattr(tools, "_RUNNER_CMD", _fake_runner_script(script))

    start = time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        await tools._spawn_runner(["q"], 5)
    elapsed = time.monotonic() - start

    # Must return within a small multiple of the 0.5s timeout; allow
    # generous margin for process startup on slow CI.
    assert elapsed < 5.0, f"_spawn_runner took {elapsed:.2f}s; kill path is too slow"


# -------- retry logic ----------------------------------------------------


@pytest.mark.asyncio
async def test_retry_count_exhausted_returns_error(monkeypatch):
    """After K+1 = _MAX_ATTEMPTS failed attempts the handler gives up."""
    monkeypatch.setattr(tools, "_SUBPROCESS_TIMEOUT_S", 0.3)
    monkeypatch.setattr(tools, "_RETRY_BACKOFF_S", 0.02)
    monkeypatch.setattr(tools, "_MAX_ATTEMPTS", 4)

    script = "import time; time.sleep(30)"  # always hangs
    monkeypatch.setattr(tools, "_RUNNER_CMD", _fake_runner_script(script))

    start = time.monotonic()
    with pytest.raises(RuntimeError, match="failed after 4 attempts"):
        await tools._web_search_with_retry(["q"], 5)
    elapsed = time.monotonic() - start

    # 4 * 0.3s timeout + 3 * 0.02s backoff + spawn overhead → ≤ ~8s on slow CI.
    assert elapsed < 10.0, f"retry loop took {elapsed:.2f}s"
    # Lower bound: must have actually waited through all attempts.
    assert elapsed > 4 * 0.3 * 0.9, (
        f"retry loop returned in {elapsed:.2f}s, suspiciously fast — "
        "did it actually attempt 4 times?"
    )


@pytest.mark.asyncio
async def test_retry_succeeds_after_transient_timeouts(monkeypatch):
    """If attempts 1-2 time out but attempt 3 returns good data, the
    retry loop returns that data without re-trying further."""
    monkeypatch.setattr(tools, "_SUBPROCESS_TIMEOUT_S", 0.3)
    monkeypatch.setattr(tools, "_RETRY_BACKOFF_S", 0.01)

    # Inject a counting fake that fails N times then succeeds. Simulating
    # this purely through _RUNNER_CMD is awkward (the subprocess can't
    # carry state across invocations); patch _spawn_runner instead so we
    # can control per-attempt behaviour precisely.
    call_count = 0
    success_bundles = [{"query": "q", "error": None, "results": []}]

    async def flaky_spawn(queries, max_results):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise asyncio.TimeoutError()
        return success_bundles

    monkeypatch.setattr(tools, "_spawn_runner", flaky_spawn)

    result = await tools._web_search_with_retry(["q"], 5)
    assert result == success_bundles
    assert call_count == 3, f"expected 3 attempts, got {call_count}"


@pytest.mark.asyncio
async def test_retry_on_subprocess_crash(monkeypatch):
    """Non-zero exit code → _RunnerFailure → retry."""
    call_count = 0
    success_bundles = [{"query": "q", "error": None, "results": []}]

    async def crashy_spawn(queries, max_results):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise tools._RunnerFailure("runner exited with code 2; stderr=...")
        return success_bundles

    monkeypatch.setattr(tools, "_spawn_runner", crashy_spawn)
    monkeypatch.setattr(tools, "_RETRY_BACKOFF_S", 0.01)

    result = await tools._web_search_with_retry(["q"], 5)
    assert result == success_bundles
    assert call_count == 2


@pytest.mark.asyncio
async def test_retry_on_malformed_json(monkeypatch):
    """Bad stdout JSON → _RunnerFailure → retry."""
    monkeypatch.setattr(tools, "_SUBPROCESS_TIMEOUT_S", 1.0)

    # First attempt: non-JSON stdout. Second: valid.
    # Use a file-backed counter since subprocess state doesn't survive.
    import tempfile
    counter = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    counter.write("0")
    counter.close()

    script = (
        "import sys, json; "
        f"p={counter.name!r}; "
        "n=int(open(p).read()); "
        "open(p,'w').write(str(n+1)); "
        "sys.stdin.read(); "
        "sys.stdout.write('this is not json' if n == 0 else "
        "json.dumps({'bundles': [{'query': 'q', 'error': None, 'results': []}]}))"
    )
    monkeypatch.setattr(tools, "_RUNNER_CMD", _fake_runner_script(script))
    monkeypatch.setattr(tools, "_RETRY_BACKOFF_S", 0.01)

    result = await tools._web_search_with_retry(["q"], 5)
    assert len(result) == 1
    assert result[0]["query"] == "q"

    import os as _os
    _os.unlink(counter.name)


@pytest.mark.asyncio
async def test_successful_subprocess_with_per_query_errors_not_retried(monkeypatch):
    """When the runner succeeds but reports per-query errors inside
    bundles (e.g. DDG rate-limited a specific query), the retry loop
    must NOT retry — it's a valid result the LLM should see."""
    call_count = 0

    async def spawn_with_query_errors(queries, max_results):
        nonlocal call_count
        call_count += 1
        return [{"query": "q", "error": "rate limited", "results": []}]

    monkeypatch.setattr(tools, "_spawn_runner", spawn_with_query_errors)

    result = await tools._web_search_with_retry(["q"], 5)
    assert call_count == 1, (
        f"expected 1 attempt but got {call_count} — successful subprocess "
        "with per-query errors was incorrectly retried"
    )
    assert result[0]["error"] == "rate limited"


# -------- backoff --------------------------------------------------------


@pytest.mark.asyncio
async def test_backoff_applied_between_retries(monkeypatch):
    """_RETRY_BACKOFF_S delay must separate retry attempts (not run them
    back-to-back flooding the upstream)."""
    monkeypatch.setattr(tools, "_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(tools, "_RETRY_BACKOFF_S", 0.15)

    attempts_at: list[float] = []

    async def timestamped_failure(queries, max_results):
        attempts_at.append(time.monotonic())
        raise tools._RunnerFailure("fake")

    monkeypatch.setattr(tools, "_spawn_runner", timestamped_failure)

    with pytest.raises(RuntimeError):
        await tools._web_search_with_retry(["q"], 5)

    assert len(attempts_at) == 3
    gaps = [attempts_at[i + 1] - attempts_at[i] for i in range(len(attempts_at) - 1)]
    for g in gaps:
        assert g >= 0.13, (
            f"expected ≥0.15s gap between attempts, got {g:.3f}s — "
            "backoff is not actually being applied"
        )


# -------- MCP handler integration ----------------------------------------


@pytest.mark.asyncio
async def test_mcp_handler_returns_error_string_when_all_retries_fail(monkeypatch):
    """The MCP tool itself must return a clean string to the LLM when
    the subprocess layer gives up — not raise, not hang."""
    async def always_fail(queries, max_results):
        raise RuntimeError("web_search failed after 4 attempts; last error: ...")

    # Short-circuit the retry function itself since we're testing the
    # handler's error-propagation contract, not retry mechanics.
    monkeypatch.setattr(tools, "_web_search_with_retry", always_fail)

    mcp = factory.create_common_tools_mcp_server()
    result = await mcp.call_tool(
        "web_search", {"queries": ["x"], "max_results_per_query": 3}
    )

    # FastMCP returns content blocks; extract text.
    # Regardless of exact shape, the error must be surfaced as text.
    as_text = str(result)
    assert "web_search failed" in as_text, (
        f"expected error message to reach LLM; got: {as_text[:300]}"
    )


@pytest.mark.asyncio
async def test_mcp_handler_formats_bundles_on_success(monkeypatch):
    """Happy path through the MCP tool: bundles → format_results → markdown."""
    fake_bundles = [
        {
            "query": "asyncio",
            "error": None,
            "results": [
                {"title": "Asyncio Docs", "url": "https://x/", "snippet": "Async IO"}
            ],
        }
    ]

    async def spawn_success(queries, max_results):
        return fake_bundles

    monkeypatch.setattr(tools, "_web_search_with_retry", spawn_success)

    mcp = factory.create_common_tools_mcp_server()
    result = await mcp.call_tool(
        "web_search", {"queries": ["asyncio"], "max_results_per_query": 3}
    )
    as_text = str(result)
    assert "Query 1: asyncio" in as_text
    assert "Asyncio Docs" in as_text
    assert "https://x/" in as_text
