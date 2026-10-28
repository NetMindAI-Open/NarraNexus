---
code_file: plugins/builtin.nexus_plugins_module/src/narranexus_plugins/nexus_plugins_module/_nexus_plugins_impl/tools.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — 私有平台模块换成公开门面（批 6c，A2-1）

本文件曾 import `narranexus.platform` 的下划线私有模块。批 6c 在拥有它的包上开出了
具名公开函数（`module_system` 的 caller-identity 解析器 / `marketplace` 的
store·pipeline·secret-box / `agent_framework.llm.prompt_probe_emit` /
`agent_framework.adapters.build_tool_policy_guard`），本文件改用它们。
理由不是命名规范：这些 MCP 工具和 helper 自批 6b 起是**独立的 wheel**，
「包内私有」对它们已经不成立了。`pyproject.toml` 的
`plugins never import a private platform module` 契约（只查直接 import）守住这条线。

## 2026-09-03（批 2f.1）— 16 个 plugin_* MCP 工具

对 `SelfExtensionService` 的薄包装：返回 JSON 字符串，异常变 `{"error": ...}`，永不 traceback。

## 2026-09-04 · no per-module port (batch 5a)

The MCP server URL comes from `mcp_server_url("<server_name>")` (the single MCP host + `/mcp/<server_name>/sse`); the module-level port constant / `self.port` and the factory's `port` parameter are gone.

2026-09-07: awareness tools `platform_overview`, `platform_slots`, `contract_docs`, `agent_self`, `capability_set` (async helpers `_arun`/`_db`).

## 2026-09-07 — honest tool signatures; per-tool cloud guard

platform_overview / platform_slots / contract_docs no longer take agent_id/user_id they never used (a signature is the model's contract; spare parameters invite invented values); capability_set takes no user_id (identity comes from caller_user_id_from_request). All five awareness tools refuse on the cloud deployment themselves (GuardError) instead of relying on the server never being mounted there.
