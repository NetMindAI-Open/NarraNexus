"""
@file_name: test_web_search_runner.py
@author: Bin Liang
@date: 2026-04-21
@description: Bug 24 — web_search_runner stdin/stdout JSON contract.

The runner is a tiny standalone entry point spawned as a subprocess
from the MCP tool handler. These tests pin the wire contract (shape
of stdin JSON, shape of stdout JSON, exit codes) and the graceful
failure modes.

Two flavours:
1. Direct: call ``_main()`` in-process with patched stdin/stdout and
   a stubbed ``search_many`` — fast, controls every axis.
2. End-to-end: launch ``python -m ...web_search_runner`` as a real
   subprocess — covers the happy path at the wire boundary. Only
   tests paths that don't require the network (e.g. empty queries,
   malformed input).
"""
from __future__ import annotations

import asyncio
import io
import json
import subprocess
import sys

import pytest

from narranexus_plugins.common_tools_module._common_tools_impl import (
    web_search_runner as runner,
)


# -------- direct _main() tests -------------------------------------------


@pytest.mark.asyncio
async def test_main_reads_stdin_calls_search_many_writes_stdout(monkeypatch):
    """_main parses stdin JSON, calls search_many, writes {"bundles": [...]}."""
    captured: dict = {}

    async def fake_search_many(queries, max_results):
        captured["queries"] = queries
        captured["max_results"] = max_results
        return [{"query": queries[0], "error": None, "results": []}]

    monkeypatch.setattr(runner, "search_many", fake_search_many)
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps({
            "queries": ["hello world"],
            "max_results_per_query": 7,
        }))
    )
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)

    rc = await runner._main()

    assert rc == runner.EXIT_OK
    assert captured == {"queries": ["hello world"], "max_results": 7}
    out = json.loads(buf.getvalue())
    assert out == {"bundles": [{"query": "hello world", "error": None, "results": []}]}


@pytest.mark.asyncio
async def test_main_defaults_max_results_when_missing(monkeypatch):
    """max_results_per_query is optional; falls back to 5 per web_search contract."""
    captured: dict = {}

    async def fake_search_many(queries, max_results):
        captured["max_results"] = max_results
        return []

    monkeypatch.setattr(runner, "search_many", fake_search_many)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"queries": []})))
    monkeypatch.setattr(sys, "stdout", io.StringIO())

    rc = await runner._main()
    assert rc == runner.EXIT_OK
    assert captured["max_results"] == 5


@pytest.mark.asyncio
async def test_main_returns_bad_input_exit_code_on_non_json(monkeypatch):
    """Non-JSON stdin → exit code EXIT_BAD_INPUT, error on stderr."""
    monkeypatch.setattr(sys, "stdin", io.StringIO("not a json string"))
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    err_buf = io.StringIO()
    monkeypatch.setattr(sys, "stderr", err_buf)

    rc = await runner._main()

    assert rc == runner.EXIT_BAD_INPUT
    assert "not valid JSON" in err_buf.getvalue()


@pytest.mark.asyncio
async def test_main_returns_bad_input_on_missing_queries_key(monkeypatch):
    """Payload missing the `queries` key → bad input."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"foo": "bar"})))
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())

    rc = await runner._main()
    assert rc == runner.EXIT_BAD_INPUT


@pytest.mark.asyncio
async def test_main_returns_internal_error_if_search_many_raises(monkeypatch):
    """If search_many itself raises, the runner exits non-zero cleanly
    rather than letting the exception kill the subprocess with a
    traceback that the parent can't parse."""
    async def boom(queries, max_results):
        raise RuntimeError("ddgs blew up")

    monkeypatch.setattr(runner, "search_many", boom)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"queries": ["x"]})))
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    err_buf = io.StringIO()
    monkeypatch.setattr(sys, "stderr", err_buf)

    rc = await runner._main()
    assert rc == runner.EXIT_INTERNAL_ERROR
    assert "ddgs blew up" in err_buf.getvalue()


# -------- end-to-end subprocess test -------------------------------------


def test_runner_invoked_as_subprocess_with_empty_queries_returns_empty_bundles():
    """Launch the runner as a real ``python -m ...`` subprocess with an
    empty queries list; must print ``{"bundles": []}`` and exit 0.

    This verifies:
      - The module path ``narranexus_plugins.common_tools_module
        ._common_tools_impl.web_search_runner`` is importable from a
        clean subprocess (no mysterious module-level side effects that
        only fire in parent).
      - stdin is read, stdout is written, the parent's JSON contract
        holds over the real wire.
      - No network hit (empty queries short-circuits in search_many).
    """
    result = subprocess.run(
        [
            sys.executable, "-m",
            "narranexus_plugins.common_tools_module."
            "_common_tools_impl.web_search_runner",
        ],
        input=json.dumps({"queries": [], "max_results_per_query": 5}),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"runner exited {result.returncode}; stderr=\n{result.stderr}"
    )
    payload = json.loads(result.stdout)
    assert payload == {"bundles": []}


def test_runner_invoked_as_subprocess_with_bad_stdin_exits_nonzero():
    """End-to-end: malformed stdin → non-zero exit, no stdout crash."""
    result = subprocess.run(
        [
            sys.executable, "-m",
            "narranexus_plugins.common_tools_module."
            "_common_tools_impl.web_search_runner",
        ],
        input="this is clearly not json",
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    # stdout should not contain a crashing traceback
    assert "Traceback" not in result.stdout
