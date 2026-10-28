---
code_file: tests/browser/test_session.py
last_verified: 2026-09-23
stub: false
---

# 无访问授权的浏览器会话

历史默认 ask/deny 不再阻止 HTTP(S) 导航，非网页协议仍在 CDP 前拒绝并写审计。
覆盖人工接管时无限等待、输入归属、视口调整、帧广播及关闭和取消后的资源释放。
FakeCdp 提供单次补帧接口；静态页面调整后补帧的行为在 test_pages 与真实浏览器测试中验证。
