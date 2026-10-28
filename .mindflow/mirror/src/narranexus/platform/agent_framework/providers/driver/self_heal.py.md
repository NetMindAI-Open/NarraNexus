---
code_file: src/narranexus/platform/agent_framework/providers/driver/self_heal.py
last_verified: 2026-10-16
stub: false
---

## 2026-10-16 (PR#413 review I1) — netmind_free 自愈跟随 env

自愈目标来自 `get_onboard_default_model`,对 `netmind_free` 即 `FREE_TIER_*_MODEL`(与全新 onboarding 一致),不再是代码常量。

## 2026-10-16 (预审修订) — 修复顺序去掉"目录默认"一步

最终顺序:onboarding 默认(在卡上)→ 卡表头 → 目录表头(空卡)。理由见 [[derive]] 同日条目(I1)。上条里"目录默认"已作废。

## 2026-10-16 — 修复目标改走 onboarding 默认(槽位种类下传)

`self_heal_if_broken` 现把 `slot["slot_name"]` 传给 `pick_default_model`,修复落到该来源 + 槽位种类的 onboarding 默认
(NetMind 两槽均为 `deepseek-ai/DeepSeek-V4.1-Flash`),而非卡表头(NetMind 表头是 omni 模型)。Claude 家族保持逻辑不变。
详见 [[derive]] 同日条目。上文 "Repair policy" 第 2 步的"取 card.models 首个"已过时,以新顺序为准。

## 2026-08-06 — 成员测试改用 effective 口径 + Claude 家族保持

`is_slot_broken` 的比对列表从 `card.models`（裸存量列）换成
`effective_card_models(card.source, card.models)`。此前 claude_oauth
老卡存量列里躺着旧全 id，钉在 `claude-sonnet-4-6` 的槽位对裸列是成员
→ 永不判 broken → CLI 永远钉旧模型（「本地CC模型name不自动更新」，
user_service.md 2026-07-30 条目声称的「override 会让 self-heal 修掉」
在本路径不成立——它比对的从来不是 get_user_config 的覆盖列表）。
修复默认值也带家族保持：`claude_family_alias(slot_model)` 命中且在
effective 列表内 → sonnet 用户修回 `sonnet`，而不是无脑取表头 opus；
识别不出家族才落回 `pick_default_model`（现传 effective 列表）。

## 2026-07-09 — table-aware writeback (agent_slots vs user_slots)

``self_heal_if_broken`` now heals into the RIGHT table: a per-agent override row
carries ``agent_id`` → repair ``agent_slots`` keyed by ``(agent_id, slot_name)``;
a plain user slot carries ``user_id`` → repair ``user_slots`` as before. Landed
WITH the resolver overlay ([[resolver]]) — without it, healing an overlaid
override would silently rewrite the user-level default it was shadowing. The
notification still goes to the owner (``card.user_id``).

# self_heal.py — reverse-validation + auto-repair

The Xiong-class bug: a ``user_slots.model`` no longer present in the
referenced ``user_providers.models`` array. The slot keeps firing
LLM calls, the provider keeps returning 4xx, the outer try/except
swallows the error, the user has no idea their PM agent stopped
working.

## Repair policy

1. Detect: ``slot.model NOT IN card.models``. Empty model is also
   "broken" (a misconfigured row).
2. Pick a safe default — first element of ``card.models``, or
   ``model_catalog.get_default_models(source, protocol)[0]`` fallback.
3. Write the new model back to the slot, set ``last_auto_repaired_at``.
4. Insert a ``user_notifications`` row of kind
   ``slot_auto_repaired`` so the user finds out at their next UI
   interaction.

## 24-hour debounce

``slot.last_auto_repaired_at`` is the cool-down clock. Within the
window we skip the repair (and the notification) and let the call
proceed with the already-rewritten model. This stops a misbehaving
slot from spamming the notification table once per LLM call. The
window is intentionally long — we want the user to see exactly one
notification per cause, not a stream.

## What we deliberately don't do

* **Sync-from-catalog is NOT triggered here.** Auto-pulling new
  models from the catalog into ``card.models`` would silently
  resurrect models the user just deliberately deleted. That belongs
  on a user-initiated "Sync available models" button.
* **No retry on the original LLM call.** We rewrite the slot for
  next time; this call is already in flight with the old model.
  The caller surfaces the error via the existing pipeline.
* **No catalog membership check on the slot.** A user who configured
  a private model on a forked endpoint should keep working — only the
  user's own card knowing about a model matters here.

## datetime gotcha

``_parse_dt`` accepts strings (SQLite return type), ``datetime`` (MySQL
via aiomysql) and ``None``. Don't replace this with ``datetime.fromisoformat``
directly — SQLite emits ``"2026-05-13 02:13:31.123456"`` (space, not
"T") which earlier Python versions reject.
