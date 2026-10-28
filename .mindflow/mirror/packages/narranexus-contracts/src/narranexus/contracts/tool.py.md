---
code_file: packages/narranexus-contracts/src/narranexus/contracts/tool.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2a）— `agent.capabilities.tools` 位的契约

`ToolProvider.list_tools()` 必须确定性且便宜（每回合调用）；工具面 append-only 保证提示前缀字节稳定。
`ToolSpec.always_visible` 默认 False：插件工具默认只经 `tool_search` 可达，因为每个常驻工具在每回合
都花上下文（spec §12）。`server` 指向提供它的 MCP server 名，空表示进程内提供。
