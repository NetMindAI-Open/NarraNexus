---
code_file: backend/routes/browser.py
last_verified: 2026-10-08
stub: false
---

## 2026-10-08（PR #410 review）

审批的两个端点删除（从未有调用方能拿到有效 id）；`GET /notices/{agent_id}` 只返回登录/验证请求。
`/runtime/install` 与 `/runtime/install/cancel` 补上与 source/mode 相同的 `_is_cloud_mode()` 闸门：
云端运行时会装进 backend 容器、会话却在 mcp 容器启动，且任何登录用户都能触发机器级下载。
不再穿透 service 私有字段：用 `install_progress` / `installing` / `forget_session`（review I6）。

2026-09-23：后端到 MCP 的内部帧通道明确设置 proxy=None，避免环境代理截走
loopback 调试流量；它不改变外部网页和安装下载的代理配置。

# Browser HTTP and WebSocket boundary

PUT /runtime/mode 仅接受 headed/headless，复用本地身份校验与统一偏好存储。云端
403、非法参数 422、存储失败 503；成功返回包含 selection.mode 的运行时状态。
模式与来源独立，保存不重启浏览器，不修改授权或登录数据；安装无需因模式保存中断。

运行时状态现在包含 selection，PUT /runtime/source 仅接受 managed/system 枚举，先
验证本地身份与部署模式，再在线程中探测并原子保存选择。禁止任意可执行路径；云端
返回 403，安装进行中返回 409。返回最新运行时状态，不重启当前浏览器或消费登录通知。

The public route owns authentication and agent ownership. HTTP handlers require
the middleware identity; approval lookup never consumes a request before its
agent has been authorized. The WebSocket authenticates its first message through
the same HTTP authentication policy, including cloud account state and Manyfold,
then checks agent ownership before contacting the module host. Origin validation
uses backend CORS configuration and same-origin matching.

The frontend connects to /ws/browser/{agent_id}?x_user_id=... and first sends
{type:"auth",user_id,token}. Local identity must match the query parameter. The
module host supplies idle, hello, control, frame and error messages. Control
remains nested and can_control is specific to this socket. Error envelopes have
code, error and retryable. Closing either relay direction drains both tasks and
closes upstream, including cancellation by the ASGI host.

The relay signs an agent-bound internal header; it never forwards the user's
credential to the browser host or exposes the internal secret to the frontend.
Local processes share a private browser-auth/stream.key under .narranexus.
Cloud backend and MCP processes must share NARRANEXUS_BROWSER_STREAM_SECRET.
The relay uses the existing MCP_BASE_URL or MCP_HOST/MCP_PORT configuration,
preserving a reverse-proxy path and translating HTTPS to WSS for cloud hosts.
The internal WebSocket explicitly disables ambient forward proxies, so a user's
HTTP_PROXY/HTTPS_PROXY settings cannot redirect the authenticated relay or break
loopback streaming. The configured MCP endpoint remains the connection target.
Backend main mounts these routes only when builtin.browser contributes a loaded
module. Runtime installation remains a machine-wide authenticated operation.

GET /approvals/{agent_id} 也返回 kind=login 的跨进程人工登录通知，字段为 id、agent_id、
session_id、reason、state 和 requested_at。读取失败返回可重试 503；没有第二条
绕过所有权的登录通知 API。站点 allow/deny 不能消费登录请求，完成只能来自内部
stream 中实际控制连接的显式交还。

Runtime status and install results expose `manual_install` from the installer's
read-only helper. It carries shell-quoted commands using this process's actual
Python, root and mirror settings; the frontend must not invent a generic system
Python command. Install results include the same help at the top level and in
their status object, including on failure. Command generation does not create a
runtime or make network requests. The real CLI entrypoint is the browser package,
not the private install module. Existing authentication and installer methods
are unchanged; these are diagnostic response fields only.

Owner-only GET/PUT /api/browser/policy/{agent_id} exposes a restricted view and
updates one {origin,capability,verdict} rule. POST to its /revoke child accepts
{origin,capability} and sets deny, including temporary-grant revocation. Both
mutations return the same agent_id/defaults/origins view as GET. Strict bodies
allow only full_cdp_access and explicit allow/deny; no raw policy document or trusted scope IDs
crosses this API. Ownership is checked before storage, and storage failures are
retryable 503 responses instead of successful-looking empty permissions.

access 规则在请求模型层返回 422，不能再添加允许、询问或禁用网站的规则。
旧 access 通知查询为空，旧 ID 无法通过答复接口恢复。所有权校验和登录通知继续有效。
