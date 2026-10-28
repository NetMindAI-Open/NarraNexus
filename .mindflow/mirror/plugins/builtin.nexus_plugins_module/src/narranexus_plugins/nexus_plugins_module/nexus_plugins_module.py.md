---
code_file: plugins/builtin.nexus_plugins_module/src/narranexus_plugins/nexus_plugins_module/nexus_plugins_module.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-03（批 2f.1）— 模块壳：指令块 + plugin_* MCP 工具

形状照 `SkillModule`：capability 模块、进 `CORE_ALWAYS_LOAD`、端口 7811、无状态 MCP server（工具收
agent_id/user_id）。指令块很短（一行流程 + 本 Agent 的插件状态摘要），控制每回合上下文成本。**只在本地生效**：
云端指令为空、`mcp_server` 返回 None、服务层直接拒绝（D1）。它自己也是插件 `builtin.nexus_plugins_module`
且 `protected`，任何工具都不能改它。

## 2026-09-04 · no per-module port (batch 5a)

The MCP server URL comes from `mcp_server_url("<server_name>")` (the single MCP host + `/mcp/<server_name>/sse`); the module-level port constant / `self.port` and the factory's `port` parameter are gone.

## 2026-09-04 · declares its platform metadata (batch 5b)

`get_config()` is static and carries what the platform used to table about this module (default / base / always-load membership, instance prefix, role, display, decision metadata as applicable).

2026-09-07: the instruction block names the self-awareness tools.
