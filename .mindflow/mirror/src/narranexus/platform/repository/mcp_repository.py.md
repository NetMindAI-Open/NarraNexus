---
code_file: src/narranexus/platform/repository/mcp_repository.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28 — `validate_mcp_sse_connection` 不再把上游内容写进 `last_error`

返回的错误文本会被路由写进 `mcp_urls.last_error` 并展示给用户。以前非 200 时读响应体
前 200 字节拼进 `HTTP <code>: <body>`，`ConnectError` / 其他异常拼 `str(e)`——门放过的
公网 host 若经 rebinding 落到内网，响应体就被读回来了；异常文本还会带出内网地址。
现在非 200 只回 `HTTP <code> from the MCP server`（**响应体根本不读**），连接错误 /
其他异常回固定文案、细节进日志；超时文案不含上游内容，保留。原有 SSRF 门与
`follow_redirects=False` 不变。测试：`tests/repository/test_mcp_repository_ssrf.py`。

# mcp_repository.py

## 2026-08-11 — `validate_mcp_sse_connection` 加 SSRF 门（安全审计 P0-3）

该函数从**服务端**去连用户/agent 提交的 URL，是典型 SSRF 面（可被指向 EC2 元数据
`169.254.169.254`、docker 网内 litellm/admin、环回）。现在**按 `enforce_public_url` 入参**（route 层按 `is_cloud_mode()` 决定，repository 不自己读部署模式）连接前先 `await assert_public_http_url(url, resolver=resolver)`（`utils/url_safety.py`）——
解析主机、任一解析 IP 落私网/环回/link-local 即拒（**解析后**校验，**缩小但不封死** DNS-rebinding 窗口——httpx 连接时会重新解析，TTL=0 可 TOCTOU 翻转，彻底封死需连接 pinning，后续项）；httpx client 显式 `follow_redirects=False`（防重定向到内网绕过）。
新增可注入 `resolver` 参数：仅测试用（注入公网 IP 免真 DNS）；两个生产调用点
（mcps.py 的 validate / validate-all）都用默认真解析，无绕过。拒绝文案通用，不回显
解析出的内网 IP。resolver 类型用 url_safety 导出的 `Resolver` 别名（`TYPE_CHECKING` 导入，自解释）。

## 2026-07-16 — update_mcp 拷贝入参;校验基线 Accept 后置(review #111 🟢×2)

`update_mcp()` 先 `dict(updates)` 再 JSON 序列化,不再改写调用方 dict。
`validate_mcp_sse_connection` 改为用户头在前、SSE 基线(Accept/Cache-Control)
后置覆盖——用户填 `Accept: application/json` 不再让健康端点校验误判失败。

## 2026-07-15 — MCP 自定义请求头(headers 列)

`mcp_urls` 表新增 `headers` JSON 列（`_json_fields` 注册），承载用户配置的
鉴权头（如 `Authorization: Bearer <token>`，需求来自 toms 的 web3 MCP）。
`add_mcp()` 接受 `headers`；`update_mcp()` 的 JSON 序列化循环同时覆盖
metadata 和 headers。`validate_mcp_sse_connection(url, headers=None)` 把自定义
头合并进 SSE 校验请求（叠在 Accept/Cache-Control 基线之上）——带鉴权的端点
不再必然 401。**headers 值是密钥：任何日志只打键名，绝不打值。**

## Why it exists

`MCPRepository` manages the `mcp_urls` table — user-configured external MCP (Model Context Protocol) server URLs that the agent can call as additional tool sources. Beyond standard CRUD, this file also contains `validate_mcp_sse_connection()`, a standalone async function (not a repository method) that validates whether an SSE endpoint is reachable. Placing it here keeps MCP-related concerns together even though the validation function does not touch the database.

## Upstream / Downstream

The MCP management API routes (`backend/routes/`) use `MCPRepository` for all CRUD operations. `AgentRuntime` reads enabled MCP URLs via `get_mcps_by_agent_user()` when building the tool set for a given execution. The MCP validation route calls `validate_mcp_sse_connection()` when the user tests an MCP connection from the settings panel.

## Design decisions

**`id_field = "id"`** (auto-increment) rather than `"mcp_id"`: same mismatch pattern as `AgentRepository`. The `get_mcp()` method queries by `mcp_id`. The `update_mcp()` and `delete_mcp()` methods build raw SQL targeting `mcp_id` explicitly.

**`validate_mcp_sse_connection()` as a module-level function**: this function uses `httpx` for streaming HTTP and is not a database operation. It could have lived in a utility module, but was placed here so the MCP route handler has a single import location for all MCP-related operations. It uses a streaming request (not a simple GET) because SSE endpoints keep the connection open indefinitely — a regular request would block.

**`update_connection_status()` delegates to `update_mcp()`**: connection status updates need to set `last_check_time` simultaneously. Routing through `update_mcp()` keeps the JSON serialization logic centralized.

## Gotchas

**`update_mcp()` builds raw SQL** using `{id_field: mcp_id}` — wait, actually it builds SQL with `WHERE mcp_id = %s`, not using `id_field`. The raw UPDATE query hardcodes `WHERE mcp_id = %s`. This is correct behavior but it means `BaseRepository.update()` is bypassed entirely for MCP updates (same pattern as `AgentRepository.update_agent()`).

**`validate_mcp_sse_connection()` has a "partial success" return**: if the HTTP status is 200 but Content-Type is not `text/event-stream`, it returns `(True, "Warning: ...")`. The caller receives `success=True` but an error message. This is intentional — the endpoint responded, just not in the expected format.

## New-joiner traps

- `MCPUrl` in `entity_schema.py` and `MCPInfo` in `api_schema.py` are structurally identical. The repository works with `MCPUrl`. The route handler converts `MCPUrl` to `MCPInfo` before returning to the frontend.
- `connection_status` is a free-form string (`"unknown"`, `"connected"`, `"failed"`). There is no enum. The initial value when creating an MCP is `"unknown"`. The validate endpoint updates it to `"connected"` or `"failed"`. Don't hardcode these strings in multiple places — read them from the `update_connection_status()` caller to see what values are actually used.
