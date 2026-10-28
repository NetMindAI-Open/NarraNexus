---
code_file: tests/agent_framework/test_self_heal_onboard_default.py
last_verified: 2026-10-16
stub: false
---

## 2026-10-16 (PR#413 round 2: N2) — 钉住 /catalog 下发的 source 口径

`get_onboard_defaults()` 的口径是"每个有 onboarding 默认的 source":测试新增断言 yunwu / openrouter 条目(agent=claude-opus-4-8、helper=gpt-5.4-mini),并注明不做 NetMind 专属白名单。

## 2026-10-16 (PR#413 review) — 补充

新增:`netmind_free` 自愈跟随 `FREE_TIER_*_MODEL`(原先与 netmind 并列断言同一常量,会反向锁死错误行为)、空 env 回落常量、
`get_onboard_defaults()` 只含来源键且 netmind_free 跟随 env、`slot_name` 必填。

# test_self_heal_onboard_default.py — self-heal 修复目标守卫

锁定 `pick_default_model` / `self_heal_if_broken` 的修复顺序(onboarding 默认 → 卡表头 → 目录表头(仅空卡);目录表头即使在卡上也不压过卡表头)。
核心用例是线上真实形态:NetMind 卡表头为 omni 模型、列表含 V4.1-Flash 但不含已消失的 V4-Flash,槽位修复必须落到 V4.1-Flash
(agent 与 helper_llm 各一),而不是表头。另含无槽位种类、无 onboarding 条目来源(普通 BYOK)的不变行为。
把 `pick_default_model` 改回"取卡表头"这些测试必须变红。
