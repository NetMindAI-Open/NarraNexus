# mcp_executor.py

Transport-agnostic MCP tool invocation utility — auto-detects WebSocket vs. SSE vs. Streamable HTTP from the URL and calls the tool.

## Why it exists

MCP servers in this project support three transports (WebSocket, SSE, Streamable HTTP), and the correct one to use depends on the server URL. Any code that calls MCP tools outside of the standard agent loop (e.g., integration tests, admin scripts, or the `AgentRuntime` calling a module's MCP tool directly) would otherwise need to duplicate the transport-selection logic. `mcp_executor.py` centralizes this into a single `mcp_tool_executor()` coroutine that any caller can use without knowing which transport the target MCP server uses.

## Upstream / Downstream

**Called by:** `agent_runtime/` when it needs to invoke MCP tools programmatically (outside the normal Claude SDK flow), integration test scripts, and any utility that needs to test MCP tool behavior in isolation.

**Depends on:** `mcp` SDK (`ClientSession`, `sse_client`, `streamablehttp_client`, `websocket_client`). No other application modules.

## Design decisions

**URL-based transport selection.** `ws://` or `wss://` → WebSocket; `http(s)://` with `/sse` in the path → SSE; `http(s)://` otherwise → Streamable HTTP. This heuristic mirrors how the MCP module runner configures its servers and avoids requiring callers to know or declare the transport.

**Tool existence check before invocation.** `mcp_tool_executor` fetches the tool list from the server and validates that the requested tool name exists before calling it. This produces a clear `ValueError` with the list of available tools rather than a cryptic server error.

**Returns the concatenated text of all `TextContent` blocks.** Image content and other block types are represented as human-readable placeholders in the returned string. This keeps the interface simple for callers that only care about text output.

## Gotchas

**The `/sse` path heuristic can misfire.** A server at `http://localhost:7800/sse-data` (path contains `/sse` but is not an SSE endpoint) would be incorrectly routed to the SSE transport. In practice all SSE MCP servers in this project use exactly `/sse` as the endpoint path, so this is not a current issue, but it is fragile to URL changes.

**No retry logic.** If the MCP server is temporarily unavailable, `mcp_tool_executor` raises a `ConnectionError` immediately. Callers that need resilience should wrap calls with `with_retry` from `retry.py`.

**New-contributor trap.** The `streamablehttp_client` context manager yields a 3-tuple `(read_stream, write_stream, _)` with a third element that `sse_client` and `websocket_client` do not yield. The code handles this with `*_` in the unpacking: `async with client_context as (read_stream, write_stream, *_)`. If you refactor this to a simpler unpacking, it will fail for Streamable HTTP.
