---
code_file: tests/browser/test_stream_bridge.py
last_verified: 2026-09-23
stub: false
---

## 2026-09-23 多页面支持

两连接可独立观看不同标签，切页不改变 Agent 目标；接管切页后跟随者收到活动页变化。
继续观看中的静止帧可回放，但最后一个观看者离开后清缓存，重连必须等待 fresh frame，
不能把已停止观察时的旧画面作为可点击的当前页面。

# 内部浏览器流回归

实际 ASGI WebSocket 与真实 Session 仲裁验证签名、连接归属、会话更换、帧回放、
输入和 resize。只有 owner 可操作，旁观连接或其断线不能释放 owner。

登录回调测试进一步区分显式 release 与 disconnect；仅成功的控制转换回调服务。
回执失败给用户 login_update_failed，同时仍允许交还控制，不让通知存储故障把
浏览器锁在人工控制中。此层 CDP 替身不作为真实画布或输入物理行为的证据。
