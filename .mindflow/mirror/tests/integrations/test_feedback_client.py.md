---
code_file: tests/integrations/test_feedback_client.py
last_verified: 2026-07-10
stub: false
---

# test_feedback_client.py

钉住 feedback_client 的对外契约：payload 形状与哈希（原始 id 绝不出现在
payload 里）、杀开关短路、URL 覆盖、非法枚举纠偏、空摘要不发、500 字截断、
异常吞没不上抛。用注入的 Stub client 断言,不打网络。
