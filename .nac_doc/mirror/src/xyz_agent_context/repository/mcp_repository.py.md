---
code_file: src/xyz_agent_context/repository/mcp_repository.py
last_verified: 2026-04-10
stub: false
---

# mcp_repository.py

## Why it exists

`MCPRepository` manages the `mcp_urls` table — user-configured external MCP (Model Context Protocol) server URLs that the agent can call as additional tool sources. Beyond standard CRUD, this file also contains `validate_mcp_sse_connection()`, a standalone async function (not a repository method) that validates whether an SSE endpoint is reachable. Placing it here keeps MCP-related concerns together even though the validation function does not touch the database.

## Upstream / Downstream

The MCP management API routes (`backend/routes/`) use `MCPRepository` for all CRUD operations. `AgentRuntime` reads enabled MCP URLs via `get_mcps_by_agent_user()` when building the tool set for a given execution. The MCP validation route calls `validate_mcp_sse_connection()` when the user tests an MCP connection from the settings panel.

## Design decisions

**`id_field = "id"`** (auto-increment) rather than `"mcp_id"`: same mismatch pattern as `AgentRepository`. The `get_mcp()` method queries by `mcp_id`. The `update_mcp()` and `delete_mcp()` methods build raw SQL targeting `mcp_id` explicitly.

**`validate_mcp_sse_connection()` as a module-level function**: this function uses `httpx` for streaming HTTP and is not a database operation. It could have lived in a utility module, but was placed here so the MCP route handler has a single import location for all MCP-related operations. It uses a streaming request (not a simple GET) because SSE endpoints keep the connection open indefinitely — a regular request would block.

**`update_connection_status()` delegates to `update_mcp()`**: connection status updates need to set `last_check_time` simultaneously. Routing through `update_mcp()` keeps the JSON serialization logic centralized.

## Gotchas

**`update_mcp()` builds raw SQL** using `{id_field: mcp_id}` — wait, actually it builds SQL with `WHERE mcp_id = %s`, not using `id_field`. The raw UPDATE query hardcodes `WHERE mcp_id = %s`. This is correct behavior but it means `BaseRepository.update()` is bypassed entirely for MCP updates (same pattern as `AgentRepository.update_agent()`).

**`validate_mcp_sse_connection()` has a "partial success" return**: if the HTTP status is 200 but Content-Type is not `text/event-stream`, it returns `(True, "Warning: ...")`. The caller receives `success=True` but an error message. This is intentional — the endpoint responded, just not in the expected format.

## New-joiner traps

- `MCPUrl` in `entity_schema.py` and `MCPInfo` in `api_schema.py` are structurally identical. The repository works with `MCPUrl`. The route handler converts `MCPUrl` to `MCPInfo` before returning to the frontend.
- `connection_status` is a free-form string (`"unknown"`, `"connected"`, `"failed"`). There is no enum. The initial value when creating an MCP is `"unknown"`. The validate endpoint updates it to `"connected"` or `"failed"`. Don't hardcode these strings in multiple places — read them from the `update_connection_status()` caller to see what values are actually used.
