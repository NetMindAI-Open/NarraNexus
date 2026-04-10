---
code_file: src/xyz_agent_context/agent_runtime/_agent_runtime_steps/step_3_direct_trigger.py
last_verified: 2026-04-10
stub: false
---
# step_3_direct_trigger.py — Pipeline Step 3 Sub-path: Direct MCP Tool Trigger

## Why It Exists

Some agent turns don't need a full LLM reasoning loop — a Job scheduler or system event already knows exactly which MCP tool to call with what arguments. This module implements that "direct trigger" path: bypass LLM, look up the target Module's MCP URL, call the tool, and return. This is faster, cheaper (no LLM tokens), and deterministic.

## Upstream / Downstream

**Called by:** `step_3_execute_path.py` when `execution_type == "direct_trigger"`

**Calls:**
- `ctx.module_service` — to resolve MCP server URL from `module_class`
- `mcp_tool_executor` — the shared utility that performs the actual MCP HTTP/stdio call

**Produces:** `PathExecutionResult` (plain `return`, not `yield`) — this function is `async def`, not an async generator

## Key Design Decisions

### Pure async def, Not a Generator
Unlike the other step_3_* files which yield `ProgressMessage`s, this is a plain coroutine. The caller (`step_3_execute_path.py`) wraps it and emits a single progress event before and after. This reflects that direct triggers are fast and don't need sub-step granularity.

### MCP URL Resolution by module_class
The trigger payload specifies a `module_class` (e.g., `"JobModule"`), not an instance ID or URL. This file resolves the live MCP server URL at runtime from `ctx.module_service`. This means the MCP server must already be running — there's no lazy-start here.

### No LLM Involvement
The tool name and arguments come directly from the trigger payload (e.g., a Job's `payload` field already parsed). This path is used when the trigger source (JobModule scheduler, external webhook) has pre-determined the exact action.

## ContextData Mutations

This step does not build or mutate `ContextData`. It operates on `ctx` fields directly:

| Field Read | Purpose |
|-----------|---------|
| `ctx.direct_trigger_payload` | Contains `module_class`, `tool_name`, `tool_args` |
| `ctx.module_service` | Used to find MCP URL |

`ctx.execution_result` is set by the router after this function returns.

## Gotchas / Edge Cases

- **MCP server not running**: If the target Module's MCP server is down, `mcp_tool_executor` raises and the entire step fails. There is no retry or fallback — caller should handle the exception and emit an error Event.
- **module_class mismatch**: If `module_class` doesn't match any running MCP server, resolution returns `None` and the call raises immediately. Check Module registration and server startup order.
- **No streaming**: Results come back as a single response dict, not a stream. Don't expect incremental output.

## Common New-Developer Mistakes

- Trying to add `yield` statements here to emit progress: use the agent_loop path instead if you need streaming progress.
- Passing `instance_id` instead of `module_class` in the trigger payload: resolution is by class, not instance.
- Forgetting that `direct_trigger_payload` must be set on `ctx` before this step is reached — the router in `step_3_execute_path.py` reads `ctx.execution_type` to decide which sub-path runs.
