---
code_file: tests/browser/test_browser_service.py
last_verified: 2026-10-08
stub: false
---

## 2026-10-08（PR #410 review）

跨进程刷新用例改为 owner 在 Settings 改脚本权限；去掉 downloads/grant。

# 浏览器服务回归

验证运行时可用性、安装恢复入口、启动参数及显式策略的权威性。独立文件能力仍验证策略刷新；
普通浏览不读取访问权限。默认 headless 测试使用临时偏好目录，既有会话不能被模式保存重启。
