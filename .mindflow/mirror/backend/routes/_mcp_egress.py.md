---
code_file: backend/routes/_mcp_egress.py
last_verified: 2026-08-11
stub: false
---

# _mcp_egress.py — MCP 运行时 SSRF 出口过滤（cloud only）

## 为什么存在

用户配置的 enabled MCP URL 会在 **agent 运行时**被 fetch，响应体进入模型上下文——所以一个解析到内网（cloud metadata 169.254.169.254、docker 网内服务、环回）的 URL 是数据外带向量，不只是 validate-time 的事。routes/agents/mcps.py 的存前筛是 **DNS-free** 的，只挡字面内网 host；一个"看着像公网、解析到内网"的名字能绕过它落库。本模块在 run 前对 enabled specs 跑真正的解析后校验（`assert_public_http_url`），websocket / skills 两个运行时消费者共用。

## 关键设计

- **cloud only（铁律 #7）**：local/桌面是单一可信用户、OS user 即边界、本地 agent 本就有 bash，localhost MCP 合法，故 local 直接透传。
- **fail-CLOSED**：`except Exception`（不只是 `UnsafeUrlError`）——`urlparse('http://[::1')` 抛的是裸 `ValueError`，若只抓 `UnsafeUrlError` 会冒到调用点的兜底 except 被吞、整份**未过滤** spec 原样进 run（安全控制失败方向必须是丢弃，不是放行）。丢弃时只 log server 名 + 异常类名，不带 URL/IP。
- **落位**：`backend/routes/_mcp_egress.py`（路由私有 helper，与 `_ownership.py` 同类），只有 websocket / skills 两个消费者。
- **仍是缓解不是消除**：和 validate-time 一样，解析后校验只**缩小** DNS-rebinding 窗口；最终 fetch 会重新解析。彻底封死要连接 pinning 到已核验 IP（后续项）。

## 边界

被过滤掉的 MCP 对本次 run 不可用（cloud）；调用方在构造 `mcp_servers` 后、传给 AgentRuntime 前调用 `filter_public_mcp_servers`。
