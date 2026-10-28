---
code_file: frontend/src/components/layout/BrowserLoginNotice.tsx
last_verified: 2026-09-23
stub: false
---

# 登录交接提示

消费后端持久化的登录请求，显示原因和等待状态。打开按钮导航到当前 agent 的聊天
并用 UI store 的 openPanel 打开浏览器，重复点击不会关闭面板。打开页面不授予站点
权限也不自动接管；控制仍由真实浏览器连接的 take/release 协议处理。

请求完成由服务端观测显式交还后更新，断开连接不算完成。前端不宣称认证成功，
不收集账号或密码，后续由 agent 重新读取页面验证。
