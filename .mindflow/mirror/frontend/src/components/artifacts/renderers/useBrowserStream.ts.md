---
code_file: frontend/src/components/artifacts/renderers/useBrowserStream.ts
last_verified: 2026-10-08
stub: false
---

## 2026-10-08（PR #410 review）

帧按自带的 `mime_type` 解码（服务端 `SCREENCAST_MIME_TYPE`），不再硬编码 `image/jpeg`；不在
`FRAME_MIME_TYPES` 里的类型直接报出 "Unsupported browser frame format: X"，而不是含糊的解码失败。

## 2026-09-24 人工新页与地址栏

new_page/navigate 仅在服务端确认控制权后发送；pageActionPending 与同步 ref 阻止
双击重复创建及等待导航时的输入。等待明确 page_action 或 page_action_failed 回执，
中途的 metadata/pages 不提前解锁；断线、idle 与重连清理 pending 状态。

## 2026-09-23 多页面支持

保存页面集合、Agent 活动页和独立观看页；帧检查 page_id，切页使未完成解码失效。
切页请求确认前不发送输入，接管与输入绑定当前 page_id，resize 去重包含页 ID。
callback ref 在 canvas 真正挂载时连接 ResizeObserver 并发送尺寸，覆盖 hello 先于
运行时状态返回的情况；仅创建 socket 时观察一次会留下缩小的旧视口。

# 实时浏览器连接

把 BrowserStreamPanel 的连接生命周期与输入 UI 分离，保证面板卸载、切换 Agent、
重新连接时旧 socket 和旧图像解码不能写回当前画布。鉴权沿用聊天 WS 的本地 user_id
及首帧 token 协议，token 不进 URL。控制权只信服务端的每连接 can_control。

无浏览器会话时接受 idle，等待后续 hello/frame；网络断线按上限 15 秒退避持续重试，
鉴权拒绝则停止自动重试并显示错误。心跳只检测传输失活，不限制 Agent 运行时长。
帧解码按接收序号防止乱序覆盖，每次断线/idle 清除本地接管状态。
页面坐标尺寸与成功绘制的那一帧 metadata 同步提交，不能被乱序解码的旧帧覆盖。
缺失或无效的尺寸才回退为图像尺寸；Chromium 正常 screencast 的位图和页面大小可能不同。

hello 和 ResizeObserver 把面板实际尺寸发送为 resize，远程网页可按窄侧栏重新布局，
而不是把 1280 像素页面压成不可读的小字。尺寸受上下限约束；其他窗口接管时本窗口
不能修改视口。重新连接或新 session 必须再次发送尺寸，不能误用旧连接的去重缓存。
