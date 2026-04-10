---
code_file: src/xyz_agent_context/schema/context_schema.py
last_verified: 2026-04-10
stub: false
---

# context_schema.py

## Why it exists

`ContextData` is the mutable accumulator that travels through the 7-step AgentRuntime pipeline. Each step reads from it and writes into it: BasicInfoModule adds `agent_name` and `creator_id`, ChatModule adds `chat_history`, GeminiRAGModule adds `rag_keywords`, and so on. By the time the pipeline reaches the LLM call, `ContextData` contains everything the agent needs to build its system prompt.

`ContextRuntimeOutput` is the final product that leaves `ContextRuntime` — the assembled `messages` list ready for the LLM SDK, the `mcp_urls` for tool routing, and a snapshot of `ContextData` for downstream hook execution.

## Upstream / Downstream

`AgentRuntime` creates a `ContextData` at the start of each execution and passes it to `ContextRuntime`. Every module's `hook_data_gathering()` receives and returns a `ContextData`. After `ContextRuntime` produces a `ContextRuntimeOutput`, `AgentRuntime` passes the embedded `ctx_data` to `HookAfterExecutionParams` so all `hook_after_event_execution()` callbacks can read the full context that was used.

## Design decisions

**`model_config = ConfigDict(extra='allow')`**: modules are free to attach arbitrary fields to `ContextData` via `ctx_data.some_new_field = value` without modifying this schema. This is the "extra\_data escape hatch" pattern. Strongly-typed fields are only defined for data used by core infrastructure; module-specific data flows through `extra_data: Dict[str, Any]` or the `extra='allow'` expansion. The risk of silent typo errors was accepted as the cost of hot-pluggability.

**`working_source` accepts both `WorkingSource` enum and raw string**: this is a pragmatic compatibility choice. Some callers (older code paths, deserialized data) pass a string; newer code uses the enum. The union type `Union[WorkingSource, str]` prevents breakage in both directions.

**`ContextRuntimeOutput` is a separate model from `ContextData`** rather than adding `messages` and `mcp_urls` directly to `ContextData`. This keeps the "input to the pipeline" (ContextData) cleanly separated from the "output of the pipeline" (ContextRuntimeOutput). Merging them would have made it ambiguous whether a field was populated before or after context construction.

## Gotchas

**`bootstrap_active` defaults to `False`** at `ContextData` construction, but it may be set to `True` by `BasicInfoModule` if the agent's awareness module detects bootstrap mode. Any code that checks `ctx_data.bootstrap_active` before `BasicInfoModule` has run in the pipeline will always see `False`.

**`narrative_id` can be `None`**: this happens on the very first interaction of a brand-new agent-user pair where no Narrative has been created yet. Narrative assignment happens in a later pipeline step; modules in `hook_data_gathering` that need `narrative_id` must guard against `None`.

## New-joiner traps

- `ContextData.extra_data` and the `extra='allow'` expansion are two separate overflow mechanisms. Fields set via `ctx_data.some_field = value` (direct attribute assignment allowed by `extra='allow'`) will appear in `model_dump()` but are not in `extra_data`. Fields set via `ctx_data.extra_data["key"] = value` live inside the `extra_data` dict. There is no automatic merging between the two.
- `ContextRuntimeOutput.mcp_urls` maps module name to URL, not `instance_id` to URL. If a module has multiple instances, only one URL appears in this dict — the one belonging to the instance active in this execution context.
