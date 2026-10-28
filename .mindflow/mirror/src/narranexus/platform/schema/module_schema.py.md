---
code_file: src/narranexus/platform/schema/module_schema.py
last_verified: 2026-09-04
stub: false
---

# module_schema.py

## Why it exists

This file originally contained all module-related data models, including instance definitions. Instance models have since been moved to `instance_schema.py`. What remains here are the structural configuration types that every module must return: `ModuleConfig` (static module metadata), `MCPServerConfig` (MCP endpoint declaration), and `ModuleInstructions` (system prompt injection). The file also re-exports `InstanceStatus` from `instance_schema.py` for backward compatibility, and carries `Trigger` and `HookCallbackResult` for the callback-driven orchestration flow.

## Upstream / Downstream

Every `XYZBaseModule` subclass implements `get_config() -> ModuleConfig` and optionally `mcp_server() -> Optional[MCPServerConfig]`. `ModuleService` reads `ModuleConfig.priority` to sort module instructions before injecting them into the system prompt. `ModuleRunner` reads `MCPServerConfig` to configure and start the MCP server process for a module. `HookCallbackResult` is returned by module hooks to tell `ModulePoller` whether to fire a downstream callback.

## Design decisions

**`ModuleConfig.module_type` as a string enum (`"capability"` vs `"task"`)**:
- `"capability"` modules (ChatModule, AwarenessModule, SocialNetworkModule) are loaded automatically based on rules — no LLM judgment required.
- `"task"` modules (JobModule) require LLM reasoning in Step 2 to decide whether to create or activate an instance.
This distinction drives the loading strategy in `ModuleService` without needing conditional logic in the module code itself.

**`Trigger` model with `TriggerType.CHAT` and `CALLBACK`**: this separates the two paths through `AgentRuntime`. A CHAT trigger blocks the user interaction; a CALLBACK trigger fires asynchronously after a dependent job completes. The `source_instance_id` and `callback_data` fields on CALLBACK triggers carry provenance so the receiving agent knows what upstream work completed.

**Legacy `ModuleInstance` class kept alongside the authoritative version in `instance_schema.py`**: this was an intentional "keep old importers working" decision when the split happened. The legacy class here has no `routing_embedding`, `keywords`, or `topic_hint` fields. Code relying on it will silently miss those fields.

## Gotchas

**`InstanceStatus` is imported from `instance_schema.py` and re-exported here**. If you do `from narranexus.platform.schema.module_schema import InstanceStatus` and `from narranexus.platform.schema.instance_schema import InstanceStatus` in the same codebase, you get the same object (not two copies). But if you compare `type(x) is InstanceStatus` with a cross-imported reference, you may see unexpected failures if the import paths ever diverge.

**`HookCallbackResult.instance_status`** should be either `COMPLETED` or `FAILED`. The `ModulePoller` uses this to decide whether to fire the downstream dependency chain or to mark dependents as `FAILED`. Returning `ACTIVE` here is a logic error that will confuse the poller.

## New-joiner traps

- The `ModuleInstance` in this file is the old version. Prefer `from narranexus.platform.schema.instance_schema import ModuleInstanceRecord, ModuleInstance`. The one here exists only so existing code does not break.
- `MCPServerConfig.type` defaults to `"sse"` and there are no other values in active use. Do not add a new type without updating `ModuleRunner`.

## 2026-09-04 · `ModuleConfig` absorbs the constant tables (batch 5b)

`ModuleDisplay` (icon / short name / desc — the former `step_display.MODULE_DISPLAY_CONFIG` row), `ModuleDecisionMeta` (capabilities / use_cases / instance_type / typical_instance_id — the former `_module_impl/metadata.MODULE_METADATA` row) and on `ModuleConfig`: `always_load`, `base`, `default`, `instance_prefix` (+ `effective_instance_prefix()`), `role`, `always_available_tools`, `context_cost_hint`. A module declares everything the platform knows about it; the platform keeps no table naming modules, so a plugin module is described exactly like a builtin.

## 2026-09-04 · `ModuleAgentInstance` (batch 5b.2)

`ModuleConfig.agent_instance` declares the public agent-level instance a module wants at agent creation (description / keywords / topic hint) — the InstanceFactory creates one per declaring module; the four core creators and the channel descriptors' `meta["agent_instance"]` are gone.

## 2026-09-04 · `discovery_hidden` / `long_running_instances` (batch 5d)

The last two platform lists about modules become declarations: bus discovery skips modules every agent has (`discovery_hidden`: awareness, basic info, message bus); the dashboard never marks a `long_running_instances` module's in-progress instances stale (skills).
