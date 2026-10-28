---
code_file: tests/browser/test_unrestricted_web_access.py
last_verified: 2026-10-08
stub: false
---

## 2026-10-08（PR #410 review）

旧审批表相关用例删除；仍钉住旧访问规则不进入模型、不能被重新创建。

# 移除网址授权回归

从真实数据库读取历史访问禁用及审批记录，验证它们不再出现在公开策略或待处理通知中，
也不能再被创建、回答或恢复。高级脚本规则独立保留；无需破坏性数据迁移。
