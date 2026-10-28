---
code_file: tests/services/test_background_llm_alerts.py
last_verified: 2026-09-23
stub: false
---

# 后台模型失败通知

使用真实内存数据库验证通知冷却、跨实例去重和不同归属的通知范围；收件箱与审计
使用替身验证内容及密钥脱敏。余额和凭据错误通知用户，短暂故障只留审计。
503 API key 通道绑定不可用的回归要求 generic 审计且零凭据失效通知。
