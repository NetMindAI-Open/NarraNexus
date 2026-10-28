---
code_file: src/narranexus/platform/browser/_browser_impl/runtime_launch.py
last_verified: 2026-09-23
stub: false
---

## 2026-09-23 多页面支持

Browser.close 前先停止页面集合监听及空白页补建，避免正常关闭时误创建新标签。
页面 worker、channel 和根连接都由同一启动/退出路径回收，只有根连接决定进程寿命。

# Browser process ownership

启动接口移除网址审批 on_ask 参数；只向会话传递独立高级能力的策略及刷新提供器。
普通访问免授权与有头/无头、正式/托管 Chrome 使用同一套实现。

Launch owns the child from spawn through endpoint discovery, transport setup and
session shutdown. Cancellation during spawn waits for the spawn result and reaps
the child. Failure at any later startup step closes CDP and stops the process.
Viewport setup and download denial are required, not best-effort promises that
could leave a blank or less restricted browser.

The subprocess stderr pipe is continuously drained through startup and the live
session, retaining only its last 16 KiB. Startup races discovery and CDP setup
against the child's return code, so a profile-lock or loader failure reports the
exit code and browser diagnostic instead of waiting out endpoint retries. This
watch is confined to startup and introduces no agent-operation deadline. Error
cleanup and normal close both finish the reader; a descendant holding the pipe
open cannot keep shutdown waiting for EOF indefinitely.

The driver discovers /json/version's browser websocket and creates an ordinary
about:blank target through Target.createTarget instead of adopting Chrome's
WebUI tab. Flattened page attachments share this browser transport.
Headless mode receives explicit metrics so its compositor actually produces
frames. Browser.setDownloadBehavior denies filesystem downloads globally because
the current tool surface does not enforce per-origin download grants.

The attached close operation is idempotent and shared, and always stops the
process even if transport close fails. A CDP-disconnection observer also closes
the session and process when the browser transport dies. Individual page closure
is handled by the page collection and leaves other tabs alive.
Termination escalates to kill and reap only for the owned Chromium process.

2026-09-23：真实 HttpOnly 登录测试证明，直接断开 CDP 并 SIGTERM 可能丢失刚收到的
持久 Cookie。正常关闭先停止接管与排队，再发送 Browser.close 并等待主进程完成 profile
落盘；只有退出协议失败或退出宽限期结束才 terminate/kill。宽限期仅用于已请求的资源关闭，
不是智能体运行时限。取消调用仍等待共享清理任务完成。

2026-09-23 实际 MCP 联调发现用户 HTTP_PROXY 使本地 /json/new 返回 502。
本进程拥有的 loopback HTTP/CDP WebSocket 明确禁用环境代理；公网安装下载仍遵循
其自身镜像与代理配置，不能为了修复本地调试连接而全局清除用户代理。
