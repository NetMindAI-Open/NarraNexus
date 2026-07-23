---
code_file: tests/marketplace/test_artifact_store_layout.py
last_verified: 2026-07-22
stub: false
---

# test_artifact_store_layout.py

S3 key-layout 解析测试:`MARKETPLACE_S3_ENV=dev|prod` 组合出
`<env>/skills` 与 `<env>/teams` 前缀(单桶多环境);显式 SKILL_S3_PREFIX /
TEMPLATE_S3_PREFIX 覆盖;不设 env 段回落扁平默认;dev/skills 与 dev/teams
key 永不相撞。
