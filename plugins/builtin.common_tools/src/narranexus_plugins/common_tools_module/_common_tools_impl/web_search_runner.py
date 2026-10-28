"""
@file_name: web_search_runner.py
@author: Bin Liang
@date: 2026-04-21
@description: Standalone subprocess entry point for web_search.

Runs as ``python -m narranexus_plugins.common_tools_module._common_tools_impl.web_search_runner``.

Reads a JSON payload from stdin, runs ``search_many()`` inside this
subprocess, writes a JSON payload to stdout, exits.

Why subprocess isolation (Bug 24, 2026-04-21)
---------------------------------------------
Bug 20 added three-layer asyncio timeouts around DDGS calls. Those
timeouts bound the *async* side cleanly but cannot reclaim the
underlying worker thread or its socket — Python has no thread-kill
primitive, so a DDGS call stuck at the C/Rust layer (primp/libcurl
CLOSE_WAIT) leaks a thread + an FD per incident. Enough leaks exhaust
the FD table and the entire MCP container becomes unable to accept new
SSE connections. Every MCP tool on that container dies with the leaker.

Subprocess isolation fixes this at the OS boundary: the parent sends
``SIGKILL`` on timeout, and Linux unconditionally reclaims every FD,
socket, and thread the subprocess held. There is no residual state.

Input (stdin, UTF-8 JSON)
-------------------------
    {
      "queries": ["first query", "second query"],
      "max_results_per_query": 5
    }

Output (stdout, UTF-8 JSON) on success
--------------------------------------
    {"bundles": [{"query": "...", "error": null | "...", "results": [...]}, ...]}

Exit codes
----------
    0  — success, stdout contains valid JSON bundles payload
    1  — input malformed (missing keys, bad JSON, etc.)
    2  — unexpected internal error (search_many itself raised)

Deliberately minimal imports: ``asyncio`` + ``json`` + ``sys`` + the
sibling ``web_search`` module (which pulls in ``ddgs`` and ``loguru``).
No NarraNexus DB / module / services imports — the subprocess must
start fast and stay light.
"""
from __future__ import annotations

import asyncio
import json
import sys

from narranexus_plugins.common_tools_module._common_tools_impl.web_search import (
    search_many,
)


EXIT_OK = 0
EXIT_BAD_INPUT = 1
EXIT_INTERNAL_ERROR = 2


async def _main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"web_search_runner: stdin is not valid JSON: {e}", file=sys.stderr)
        return EXIT_BAD_INPUT

    try:
        queries = list(payload["queries"])
        max_results = int(payload.get("max_results_per_query", 5))
    except (KeyError, TypeError, ValueError) as e:
        print(f"web_search_runner: invalid payload shape: {e}", file=sys.stderr)
        return EXIT_BAD_INPUT

    try:
        bundles = await search_many(queries, max_results)
    except Exception as e:  # noqa: BLE001 — surface any internal crash to parent
        print(f"web_search_runner: search_many raised: {e!r}", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    json.dump({"bundles": bundles}, sys.stdout, ensure_ascii=False)
    sys.stdout.flush()
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
