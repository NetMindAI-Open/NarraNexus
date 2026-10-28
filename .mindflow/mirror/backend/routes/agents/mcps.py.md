---
code_file: backend/routes/agents/mcps.py
last_verified: 2026-08-12
stub: false
---

# agents/mcps.py — MCP URL 管理与连接验证路由

## 2026-08-12 — create_mcp 补 ownership（SEC-06，Mark IDOR 批）

`create_mcp` 是本文件里唯一信任 URL 上 `agent_id`、不校验归属的路由——任何登录用户可给他人 agent 写一条 MCP 配置（任意 URL），受害者看不到也删不掉。update/delete/validate 早有 owner 校验，本次给 create 在 `try` 之前补 `assert_owned(request, agent_id)`（放 try 外让 HTTPException 透出、不被 `except` 压成 MCPResponse）。SSRF 存前屏蔽（下条）是另一半防护，二者正交。

## 2026-08-11 — 存前 SSRF 屏蔽 + 内部错脱敏（安全审计 P0-3/P2-2）

create/update MCP 现在在入库前用 `_blocks_internal_url(url)`（**cloud only**，铁律 #7：local/桌面单可信用户跑 localhost MCP 合法故不挡；DNS-free 便宜筛，
基于 `narranexus/platform/utils/url_safety.is_obviously_non_public_url`，**parse-safe**——畸形/非串 URL 判为不安全而非抛异常）挡掉明显内网
目标——字面私网/环回/link-local IP、localhost、`.local`、以及像 `narranexus-litellm`
这种单标签 docker 服务名——让它们**根本存不进库**（运行时读取方也就读不到）。这是便宜
的存前筛，不是边界；真正的解析后边界在 `validate_mcp_sse_connection`（连接时，route 传 `enforce_public_url=is_cloud_mode()`）。各
`except` 的 `error=str(e)` 收敛为通用文案，内部异常只进日志（原始 URL/内网 IP 不回客户端）。

## 2026-07-16 — 掩码只保留空格分隔的 auth scheme 前缀(review #111 🟢)

原 `v[:6]` 前缀对 `X-API-Key: sk-live-…` 这类无 scheme 值会露出真实密钥前缀。
现在仅当值形如 `<Alpha> <rest>`(Bearer/Basic…)保留 scheme,其余只留末 4 位。

## 2026-07-15 — headers 透传 + 掩码回显 + 修复 update 的 TypeError

- Create/Update 请求体接受 `headers`（dict）；Update 语义=字段出现即整组覆盖
  （`model_fields_set` 判别），字段缺席不动。
- 读接口一律经 `_masked_headers()` 掩码（`v[:6]…v[-4:]`，短值全 `****`）——
  明文 token 绝不出现在 GET 响应里。
- validate 端点把存储的 headers 传给 `validate_mcp_sse_connection`。
- 顺手修复既有 bug：原 PUT 路由用 `name=/url=` kwargs 调
  `repo.update_mcp(mcp_id, updates: Dict)`，必然 TypeError——现在先组 updates
  dict 再调用（前端 enable/disable toggle 此前实际是坏的）。

## 为什么存在

Agent 可以通过 MCP（Model Context Protocol）协议连接外部工具服务器。每个 `agent_id + user_id` 组合可以配置多个 MCP URL，这些 URL 在 WebSocket 端点里被加载并传递给 `AgentRuntime`。这个路由提供 MCP 配置的完整 CRUD，以及 SSE 连接有效性验证。

## 上下游关系

- **被谁用**：`backend/routes/agents/core.py` 聚合；前端 MCP 配置面板
- **依赖谁**：
  - `MCPRepository` — MCP 记录的增删改查
  - `narranexus.platform.repository.mcp_repository.validate_mcp_sse_connection` — 实际的网络连通性测试
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
