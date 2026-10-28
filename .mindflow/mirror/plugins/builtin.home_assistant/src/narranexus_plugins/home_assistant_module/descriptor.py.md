---
code_file: plugins/builtin.home_assistant/src/narranexus_plugins/home_assistant_module/descriptor.py
last_verified: 2026-09-04
stub: false
---

# home_assistant_module/descriptor.py — credentials-only channel

## Intent

Home Assistant has no inbound transport (`transport="none"`, no trigger) but the data-access channel seam serves its credential manager, so it is a descriptor too; it registers no WorkingSource.

## 2026-09-04 · module_ref + agent instance (batch 4e)

Declares `module_ref` (HomeAssistantModule) and `meta["agent_instance"]` so the instance factory creates the per-agent HomeAssistantModule instance from the descriptor instead of a hard-coded `_create_home_assistant_instance`.

## 2026-09-04 · no `agent_instance` meta (batch 5b.2)

The module declares its agent-level instance in its own `ModuleConfig`.
