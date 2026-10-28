---
code_file: tests/agent_framework/test_claude_oauth_slot_self_heal.py
last_verified: 2026-10-16
stub: false
---

## 2026-10-16 — 兄弟测试

`test_non_family_garbage_on_claude_oauth_heals_to_list_head` 仍成立(claude_oauth 无 onboarding 条目,目录默认 = 表头 `opus`)。
NetMind 修复目标另见 `test_self_heal_onboard_default.py`。

# test_claude_oauth_slot_self_heal.py — self-heal 用 effective 口径判 broken

钉住「本地CC模型name不自动更新」（Base recvqEiNbacKWa）的修复：resolver
路径的卡是 `ProviderCard.from_row` 裸列，claude_oauth 老卡存量列里躺着旧
全 id，钉在 `claude-sonnet-4-6` 的槽位对裸列是成员、永不判 broken。修复
后成员测试对 `effective_card_models`（别名表）跑：

- 旧全 id 槽位 → 判 broken → **家族保持** heal（sonnet-4-6→sonnet、
  opus-4-1→opus、haiku-4-5→haiku），落库 + 通知；
- 已是别名的槽位 → 健康，不动、不发通知；
- 识别不出家族的垃圾值 → 落回表头（opus）；
- 非 OAuth 卡（source=user 私有模型）→ 保持裸列语义，不误伤。

另含 `effective_card_models` 单元断言：claude_oauth 无视存量返回别名表、
codex_oauth 返回 curated 表、其他 source 原样返回存量。
