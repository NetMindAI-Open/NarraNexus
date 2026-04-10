---
code_file: src/xyz_agent_context/schema/hook_schema.py
last_verified: 2026-04-10
stub: false
---

# hook_schema.py

## Why it exists

Every module in the system has a `hook_after_event_execution()` callback that fires after the agent finishes a turn. Originally this hook received a pile of `**kwargs` which made it impossible to know what was actually available without reading the caller. This file replaces those kwargs with typed dataclasses: callers construct a `HookAfterExecutionParams` and modules destructure it in a type-safe way.

`WorkingSource` is also defined here — the enum that identifies what kind of execution triggered the current turn (chat, job, a2a, callback, etc.).

## Upstream / Downstream

`AgentRuntime` (Step 8) constructs `HookAfterExecutionParams` from the `PathExecutionResult` and fires `HookManager.hook_after_event_execution()`. Every module's hook implementation receives a single `HookAfterExecutionParams` argument. `WorkingSource` is imported by `context_schema.py` (`ContextData.working_source`) and by the narrative system to decide how to update summaries differently for chat vs job executions.

## Design decisions

**Three nested dataclasses (`HookExecutionContext`, `HookIOData`, `HookExecutionTrace`) instead of one flat dataclass**: this grouping reflects what different kinds of modules need. A lightweight module might only need `execution_ctx` (who/where/what). A heavy analysis module like `JobModule` additionally needs `trace` (the raw agent loop response to parse tool calls). The nesting means a module can assert `if params.trace is None: return` and skip expensive processing entirely.

**`HookAfterExecutionParams.event` and `narrative` fields**: these were added specifically for EverMemOS-style memory writing that needs the live Narrative and Event objects (not just their IDs). Rather than adding another layer of nesting, they sit directly on the params struct.

**Convenience properties on `HookAfterExecutionParams`**: `params.event_id`, `params.final_output`, `params.event_log` etc. are pass-through properties that flatten the nesting for the common case. The nesting is there for type clarity but should not force every module to write `params.execution_ctx.event_id`.

**`WorkingSource` inherits from `str`** so it compares equal to its string value in legacy code paths that still use raw strings. This was a deliberate bridge choice during migration.

## Gotchas

**`HookExecutionTrace` is `Optional` in `HookAfterExecutionParams`**. For `DIRECT_TRIGGER` executions, `trace.agent_loop_response` is always an empty list and may not be set at all. Any module that accesses `params.agent_loop_response` without checking for `None` first will get an empty list via the property (safe), but direct attribute access via `params.trace.event_log` will raise `AttributeError` if `trace` is `None`.

**`WorkingSource.MESSAGE_BUS`** is not yet wired to a concrete trigger implementation. It exists as a reservation. If you see `working_source == "message_bus"` in production data, something set it explicitly and there is no standard handler for it yet.

## New-joiner traps

- `WorkingSource.is_automated()` includes `MATRIX` and `MESSAGE_BUS`. This means Matrix messages are not treated as "user-initiated" even though a human sent them. The distinction matters for Narrative summary strategies — automated executions generate briefer summaries by default.
- Do not confuse `HookAfterExecutionParams.instance` (the `ModuleInstance` that is currently executing) with `ctx_data.extra_data.get("job_id")` or similar module-specific context. The `instance` field is the generic module instance; module-specific state must be retrieved from `ctx_data.extra_data`.
