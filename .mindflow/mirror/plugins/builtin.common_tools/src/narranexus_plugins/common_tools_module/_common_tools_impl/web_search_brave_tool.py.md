---
code_file: plugins/builtin.common_tools/src/narranexus_plugins/common_tools_module/_common_tools_impl/web_search_brave_tool.py
last_verified: 2026-04-21
stub: false
---

# web_search_brave_tool.py — Brave Search API backend for the web_search MCP tool

## Why it exists

CommonToolsModule needs two interchangeable `web_search` backends: one for
environments where `BRAVE_API_KEY` is set (this file), one for environments
without (DDGS fallback). This file is the Brave side of that split.

Unlike `web_search_ddgs_tool.py`, which wraps a synchronous library and
requires subprocess isolation to handle stuck threads, this file uses
`httpx.AsyncClient` — a natively async HTTP client that respects
`asyncio.CancelledError`. That means per-query and per-gather `asyncio.wait_for`
calls are sufficient; no subprocess spawning is needed.

The file exists as a standalone module (instead of being inlined into
`_common_tools_mcp_tools.py`) so the factory can do a lazy import only when
`BRAVE_API_KEY` is present, avoiding the `httpx` import cost in DDGS-only
environments, and keeping each backend independently testable.

## This file does NOT do

- It does not decide which backend to activate — that is `_common_tools_mcp_tools.py`'s
  job (`create_common_tools_mcp_server` reads `BRAVE_API_KEY` and calls `register`).
- It does not format results — `format_results` is imported from `web_search.py`
  and shared with the DDGS path.
- It does not handle rate limiting via a queue or token bucket — it retries K=3
  times with linear backoff and then raises, letting the MCP handler surface the
  failure inline.

## Upstream / Downstream

- **Called by**: `_common_tools_mcp_tools.create_common_tools_mcp_server` via lazy
  import (`from ._common_tools_impl.web_search_brave_tool import register`). Called
  only when `BRAVE_API_KEY` is non-empty.
- **Calls**: `httpx.AsyncClient` for HTTP; `web_search.format_results` for
  rendering bundles; `_common_tools_mcp_tools.with_mcp_timeout` for the outermost
  handler deadline.

## Design decisions

- **Native async httpx over subprocess**: DDGS wraps a synchronous C/Rust HTTP
  layer that cannot be cancelled at the asyncio level. Brave's REST API is
  simple JSON-over-HTTPS; httpx handles it natively, so `asyncio.wait_for` is
  enough. Subprocess isolation would add complexity with no benefit here.
- **Three timeout layers**: `httpx.Timeout` (connection/read bounds) +
  `asyncio.wait_for` per query (`_PER_QUERY_TIMEOUT_S=10s`) + `asyncio.wait_for`
  on `gather` (`_OVERALL_TIMEOUT_S=25s`). Mirrors the DDGS three-layer defense
  documented in `web_search.py`.
- **Retry only on 429 and 5xx**: 401 (bad key) and 4xx client errors are not
  retry-eligible — they surface directly in the bundle's `error` field.
- **count capped at 20**: Brave's API maximum is 20 results per query. Values
  above 20 are silently clamped.

## Gotcha / Edge cases

- **`asyncio.wait_for` wraps the `client.get()` coroutine, not the full
  `async with` block**: If the timeout fires while `client.get()` is awaiting,
  httpx cancels the connection correctly. However, the `async with httpx.AsyncClient`
  context manager in `_search_many_brave` remains open until all per-query
  coroutines either complete or are cancelled. This is intentional — shared
  connection pooling.
- **`_BraveRateLimited` / `_BraveServerError` are module-private**: They bubble
  up through `asyncio.gather` (because `gather` re-raises the first exception by
  default), get caught in `_search_with_retry`, and trigger the retry loop. If
  you add a per-query `return_exceptions=True` to gather, these would silently
  land as bundle results instead — don't do that.
- **monkeypatching in tests patches `brave.httpx.AsyncClient`** (the module-level
  reference), not `httpx.AsyncClient` globally. If you restructure imports
  (`from httpx import AsyncClient`), the patch target changes and tests will hit
  the real network.

## Related constraints

- Iron rule #3 — Module independence: this file imports only from within
  `common_tools_module` and standard libraries. No cross-module imports.
- See `_common_tools_mcp_tools.py.md` for the factory dispatch logic.
- See `web_search_ddgs_tool.py.md` for the DDGS counterpart and the rationale
  for subprocess isolation (which this file intentionally avoids).
