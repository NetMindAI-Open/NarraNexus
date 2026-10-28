---
code_file: tests/marketplace/test_skill_seed.py
last_verified: 2026-08-19
stub: false
---

# test_skill_seed.py

Seeds a fixture marketplace_skills/ tree (one default + one normal): asserts
both publish, is_default preserved, blob in store, list_defaults returns only
the default; idempotency (second pass re-publishes nothing, same hash);
no-op when the dir is missing; and a check against the REAL repo that
netmind-vision/netmind-transcribe exist with default=true.

2026-08-19（onboarding 引导 Agent 批）新增两条真实仓断言：
- `test_real_repo_guide_skill_is_vendored_and_not_default` — narranexus-guide
  随包 vendored 且 manifest `default:false`（不给普通新 agent 自动装）。
- `test_real_guide_skill_survives_the_publish_scan_into_the_catalog` — 用
  REAL vendored 树跑 seed，钉 catalog 里有 `narranexus-guide@1.0.0`、非
  default、blob 在 store：publish 的安全扫描拒绝是静默的（warning+skip），
  没有这条守卫的话引导 Agent 的 awareness 会指向一个永远装不上的 SKILL.md。
