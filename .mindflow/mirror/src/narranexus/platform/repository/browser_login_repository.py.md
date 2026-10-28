---
code_file: src/narranexus/platform/repository/browser_login_repository.py
last_verified: 2026-09-23
stub: false
---

# 人工登录通知存储

MCP 持有浏览器，API 展示通知，因此用共享表保存登录需求。它不是授权机制，不能
写 permission policy，也不收集登录凭证。外部形状使用 request ID 与 agent 的面板
标识；内部 session_key 和 connection_id 把交还绑定到同一个活会话与实际控制者。

pending 到 in_control 由服务控制回调推进；仅匹配连接的显式 release 完成，断线
恢复 pending。完成行留到等待工具读到，再由工具退役；取消和会话清理由服务负责。
数据库失败向上抛出，不能返回空列表伪装成通知已处理。
