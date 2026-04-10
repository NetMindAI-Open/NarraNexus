---
code_file: backend/routes/agents_mcps.py
last_verified: 2026-04-10
stub: false
---

# agents_mcps.py — MCP URL 管理与连接验证路由

## 为什么存在

Agent 可以通过 MCP（Model Context Protocol）协议连接外部工具服务器。每个 `agent_id + user_id` 组合可以配置多个 MCP URL，这些 URL 在 WebSocket 端点里被加载并传递给 `AgentRuntime`。这个路由提供 MCP 配置的完整 CRUD，以及 SSE 连接有效性验证。

## 上下游关系

- **被谁用**：`backend/routes/agents.py` 聚合；前端 MCP 配置面板
- **依赖谁**：
  - `MCPRepository` — MCP 记录的增删改查
  - `xyz_agent_context.repository.mcp_repository.validate_mcp_sse_connection` — 实际的网络连通性测试
- **被间接用到**：`backend/routes/websocket.py` 在每次 agent run 前通过 `MCPRepository` 加载已启用的 MCP URL

## 设计决策

**URL 格式校验在路由层做**

创建和更新时会检查 URL 必须以 `http://` 或 `https://` 开头。这是最简单的 URL 格式验证，没有用 Pydantic 的 `HttpUrl` 类型，因为 `HttpUrl` 在 Pydantic v2 里会对 URL 做规范化处理（去掉末尾斜杠等），可能影响 MCP 服务器的实际连接。

**批量验证并行执行**

`validate-all` 接口使用 `asyncio.gather` 并行验证所有 MCP 连接，而不是串行。这对有多个 MCP 的场景效率更高，但如果某个 MCP 验证超时时间较长，会阻塞所有结果返回。`validate_mcp_sse_connection` 内部应该有超时控制（在核心包里实现）。

**所有权校验**

更新、删除、验证操作都会先拿到 MCP 记录，检查 `agent_id` 和 `user_id` 是否匹配，再执行操作。这防止用户通过猜 `mcp_id` 操作别人的 MCP 配置。

## Gotcha / 边界情况

- **创建后立即重查**：`create_mcp` 在 insert 后调用 `repo.get_mcps_by_agent_user` 拿所有 MCP 再用 `id == record_id` 找到刚创建的那条。如果 `record_id` 和 MCP 列表对不上（比如 Repository 实现里 `add_mcp` 返回的是自增主键而不是 `mcp_id`），`created_mcp` 可能是 None，响应里 `mcp` 字段为空但 `success=True`。
- **`validate_mcp_sse_connection` 的错误处理**：它返回 `(connected, error)` 元组，成功时 `error` 为 None，失败时 `connected` 为 False。验证结果会更新数据库里的 `connection_status` 字段，但不会影响 `is_enabled` 状态——连接失败的 MCP 仍然保持 enabled，只是状态标记为 "failed"。

## 新人易踩的坑

`is_enabled=True` 才会在 agent run 时被 WebSocket 端点加载。在 MCP 配置界面里关闭（`is_enabled=False`）的 MCP，即使连接状态是 "connected"，也不会被 Agent 使用。这是预期行为，但调试时容易疑惑"为什么 MCP 能 ping 通但 Agent 没有用它"。
