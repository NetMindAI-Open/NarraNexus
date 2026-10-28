---
code_file: src/narranexus/platform/browser/_browser_impl/cdp.py
last_verified: 2026-10-08
stub: false
---

## 2026-10-08（PR #410 review）

帧编码收敛为 `SCREENCAST_FORMAT` / `SCREENCAST_MIME_TYPE` 一个来源，screencast 与首帧补拍共用；
session 把 mime_type 写进每帧，前端据此解码。

## 2026-09-23 多页面支持

根连接通过 sessionId 复用多个页面 channel，命令 ID 全局关联，帧和确认保留自己的
channel。页面断开只结束对应命令；根连接失败才结束所有页面。观看时启用 focus
emulation，使后台标签继续绘制，不抢占 Agent 原生前台页。先截取首帧，再接 push
screencast；最后一个观看者退出时停止该页流并恢复 focus，不降级为截图轮询。
静态页缩放后 Chrome 不保证产生 compositor 帧；capture_frame 复用当前画质与帧回调，
在启动和视口变化时主动刷新一次，之后继续事件推流。

# CDP transport and input translation

The reader starts before correlated commands can wait for responses. Its exit
marks the transport closed and fails pending calls. Close drains the reader and
frame acknowledgements; failed sends and cancelled calls cannot leave pending
futures behind. The process owner observes the browser transport's wait_closed;
a page channel closing does not end the browser process.

Every screencast frame is acknowledged, including late frames after a subscriber
has stopped listening. Static pages legitimately emit no new frames; the session
replays its latest image to new viewers instead of interpreting silence as a
dead renderer.
Screencast bounds match the maximum panel viewport (1920 by 1440), avoiding
implicit downscaling when a resized panel exceeds the original launch dimensions.

Input is a whitelist, never arbitrary protocol forwarding. Coordinates must be
finite. Mouse movement defaults to no button and no click; drag state, click
counts, wheel deltas and modifiers are preserved. Keys carry code and virtual
key codes, while key-up, raw keydown and shortcuts do not insert printable text.
Explicit text events use Input.insertText for paste and IME.

2026-09-23：真实登录表单回归发现，仅发送 Enter 键码不会触发 Chromium 原生提交。
普通 Enter keyDown 需携带回车文本；keyUp、rawKeyDown 和快捷键仍不插入文本。
这一规则同时覆盖输入框提交与多行文本换行，不能把所有非打印键统一清空。
