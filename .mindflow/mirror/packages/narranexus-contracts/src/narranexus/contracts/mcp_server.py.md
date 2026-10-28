---
code_file: packages/narranexus-contracts/src/narranexus/contracts/mcp_server.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2a）— `agent.capabilities.mcp_servers` 位的契约

站点级 MCP server，与用户按 Agent 配置的 `MCPUrl` 并存。三种传输互斥校验（stdio 要 command 不要 url，
反之亦然）。`to_config()` 输出与用户配置的 server 同一形状，让各框架适配器无需区分来源。
