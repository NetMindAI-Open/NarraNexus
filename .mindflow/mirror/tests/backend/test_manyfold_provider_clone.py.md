---
code_file: tests/backend/test_manyfold_provider_clone.py
last_verified: 2026-07-18
stub: false
---

# test_manyfold_provider_clone.py

钉住 [[manyfold_agents]] `_clone_provider_setup` 的 netmind-only 过滤
（2026-07-18 review 修复）：云端克隆只带 netmind 卡、指向被过滤卡的 slot
一并跳过（不留悬空引用）；本地克隆不过滤、全量复制。直接单测 helper
（真内存库），不走路由（省去网关 token 中间件）。