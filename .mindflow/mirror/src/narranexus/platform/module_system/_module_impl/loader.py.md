---
code_file: src/narranexus/platform/module_system/_module_impl/loader.py
last_verified: 2026-09-04
stub: false
---

## 2026-09-04（批 3c.1）— `CORE_ALWAYS_LOAD` 派生自贡献表 `always_load`

`always_load_modules(module_map)` 只保留 map 里还在的核心常驻 + 渠道模块（禁用感知）。

## 2026-09-03（批 2f.1）— `NexusPluginsModule` 进 `CORE_ALWAYS_LOAD`

云端它是空指令、无 MCP 的 no-op 实例（D1）。

## 2026-09-04 · no LarkModule prefix entry (batch 4e)

The prefix map lists core modules only; `LarkModule` → `lark` comes from the generic rule.

## 2026-09-04 · lists from declarations (batch 5b)

`DEFAULT_MODULE_LIST` → `default_modules(module_map)` (modules declaring `default=True`, by priority); `CORE_ALWAYS_LOAD` / `ALWAYS_LOAD_MODULES` → `always_load_modules(module_map)` = modules declaring `always_load=True` + every ChannelModuleBase subclass; the instance-id prefix map → `instance_prefix_for`.

## 2026-09-04 · no JobModule literal (batch 5b.2)

`_ensure_job_module_available` → `_ensure_always_available_tool_modules` (every module declaring `always_available_tools`); the supplemented job instance uses the module declaring role "jobs".

## 2026-09-04 · only enabled capabilities bind (batch 5c)

`_enabled_map` (via `CapabilityService`) and `_drop_disabled` run before `_create_module_objects` on the decision path, the fast path and the traditional list: instances of modules the owner disabled — or plugin modules never enabled — never bind, so their instructions, tools and hooks stay out of the turn; the budget warning fires here. No database client (unit tests) → nothing is filtered.
