---
code_file: tests/backend/test_providers_catalog_onboard_defaults.py
last_verified: 2026-10-16
stub: false
---

# test_providers_catalog_onboard_defaults.py — /catalog 的 onboard_defaults 守卫

直接调 `get_catalog()`:netmind / netmind_free 均为 V4.1-Flash、不含 anthropic/openai 协议键;`FREE_TIER_AGENT_MODEL` 设置后 netmind_free.agent 跟随而 netmind 不变。
删掉路由里的 `onboard_defaults` 字段这些测试必须变红。
