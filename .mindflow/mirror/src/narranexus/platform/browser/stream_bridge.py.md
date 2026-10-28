---
code_file: src/narranexus/platform/browser/stream_bridge.py
last_verified: 2026-09-24
stub: false
---

## 2026-09-24 人工标签操作

new_page 与 navigate 命令仅当前 owner 可执行；导航要求请求页等于当前选中页。
成功先刷新 pages/订阅再发 page_action 回执，失败发 page_action_failed，前端不因
无响应卡住。跟随回到 owner 活动页，旁观连接仍保持独立选择。

## 2026-09-23 多页面支持

hello/pages 携带 pages、active_page_id、selected_page_id、following_active。页面集合
事件唤醒连接刷新；默认跟随活动页，select_page 固定旁观目标，follow_active 恢复跟随。
owner 切页同时改变活动页，交还后 Agent 继续使用该页；只有 owner 可关闭标签。
每个连接独立观看，切换先清待发旧帧、再更换订阅，最后一位观看者退出才停止该页流。
frame/input/resize 绑定 page_id；元数据先于画面发送，旧页输入不得落到新页。

# Internal session stream

This route lives beside the session in the MCP host and accepts only the
backend's short-lived agent-bound signature. Browser Origin headers are refused,
so serving on localhost never makes this a browser-accessible CDP bypass. It is
protected independently of the MCP middleware's HTTP-only identity checks.

A missing or closed session emits idle/awaiting_session and keeps the socket
connected. Session existence is checked periodically; actual frames remain
push-driven. A new session sends hello before frames, with a connection ID and
nested control state. Reconnect subscribes to the session's cached last image.

Panels send {type:"resize",width,height} on hello and container size changes.
The bridge passes its own connection ID to the session; the caller cannot choose
an owner ID. Resize messages before a session exists are ignored, so hello must
resend dimensions. Malformed or unauthorized requests are ignored without an
acknowledgement; successful resize is reflected in subsequent frame metadata.
CDP resize failures emit error/resize_failed with retryable true.

can_control is true only for the owning socket. Every subscriber receives control
broadcasts, and only the owner can release. Disconnect unsubscribes the viewer
and resets its held input before release. The frame slot coalesces complete
images without evicting control/error messages. A stalled control consumer is
disconnected explicitly instead of silently losing control messages. Both pumps
and the session follower are awaited during cancellation-safe cleanup.

登录完成通过可注入 on_login_control 回调接到持有会话的服务。只有 take_control/
release_control 实际成功才回调 take/release；清理连接时回调 disconnect，而不是
release。旁观连接不能完成登录。回执异常作为 login_update_failed 通知前端，控制
仍能交还；服务负责让等待工具得到错误。stream 不接收前端自报的“登录完成”状态。
