---
code_file: tests/backend/test_cloud_netmind_only_slots.py
last_verified: 2026-07-17
stub: false
---

# test_cloud_netmind_only_slots.py

钉住 providers 路由的云端 netmind-only 槽位策略边界（规则真源
[[cloud_policy]]，2026-07-18 起在 `set_slot` 内强制）：

- **PUT /slots**：真内存库 + 真 `UserProviderService`（策略下沉服务层后
  stub service 测不到任何东西）——云端非 staff × 自有卡 403 且零写入、
  netmind / staff / 本地 200、卡不存在 400 not-found（策略不得掩盖
  not-found）。种子行必须含 `linked_group:""` 等全字段（成功路径会经
  `get_user_config` 过 Pydantic）。
- **POST /onboard**（stub，路由级行为）：云端非 staff activate=False
  （register-only），staff 与本地 True。
- **POST /**（stub）：netmind-only 时跳过 default_slots。

per-agent 写入器的同款策略 + 框架钉选门禁测试在
test_agents_llm_config_routes.py；manyfold 克隆过滤在
test_manyfold_provider_clone.py。
