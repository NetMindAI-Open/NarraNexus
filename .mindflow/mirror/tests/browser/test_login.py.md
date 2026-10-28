---
code_file: tests/browser/test_login.py
last_verified: 2026-10-08
stub: false
---

## 2026-10-08（PR #410 review）

随服务改为 `pending_notices`；去掉对审批 resolve 的断言。

# 登录交接回归

真实 service/session 与隔离数据库验证跨进程通知、实际 owner 的显式交还、断线、旁观者、
取消及会话替换。历史 allow/ask/deny 均不阻止交还后的读取，跨站跳转无需访问授权。
通知创建不穿过人工控制读取页面，交还不等于成功登录。存储失败和真实 Chrome 表单另有覆盖。
