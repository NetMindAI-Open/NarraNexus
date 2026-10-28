---
code_file: src/narranexus/platform/agent_framework/providers/driver/derive.py
last_verified: 2026-10-16
stub: false
---

## 2026-10-16 (PR#413 review M1) — slot_name 必填

`pick_default_model` 的 `slot_name` 去掉默认值:漏传会静默退回"取卡表头"(即 2026-09-11 事故行为),唯一调用方 self_heal 已传。
无槽位种类(如 embedding)请显式传 `None`。

## 2026-10-16 — pick_default_model 优先 onboarding 默认,不再取 card.models 表头

**根因**:NetMind 卡的 models 列表来自同步目录,表头是 `Qwen/Qwen3.5-Omni-Flash`。旧策略"取卡表头"让
self-heal 把一批槽位(2026-09-11 单次 34 个 helper 槽,线上约 50 个)悄悄改指到多模态 omni 模型。
(触发:V4-Flash 从 NetMind 公开目录消失,槽位 pin 的模型不在卡上 → 被判 broken → 取表头。)

**顺序**:onboarding 默认(`get_onboard_default_model(source, slot_name)`,仅 agent / helper_llm 有,须在卡上)→ 卡表头 →
目录表头(仅空卡)→ None。**不**把"目录默认 `get_default_models()[0]`"排在卡表头前(预审 I1 删除了该步):它是已提交账本快照
(云上只读 JSON、键排序)的字母序表头——可能是 32k 上下文的 `Qwen/Qwen2.5-7B-Instruct`(2026-07-13 的 400 事故模型),
对普通 `user` BYOK 卡则会选 gpt-5.5 / claude-opus-4-8 压过用户自己的列表。
通用查表,无 NetMind 特判;没有 onboarding 条目的来源(普通 `user` BYOK)行为不变。`slot_name` 为空(如 embedding)
时跳过第一步,不会把 agent 默认当成 embedding 默认。守卫:`tests/agent_framework/test_self_heal_onboard_default.py`。

## 2026-09-10 — 订阅集合改 import `SUBSCRIPTION_AUTH_TYPES`（PR#392 复审 I2）

订阅运输层走 Anthropic 自身计费（不扣额度）的判定原先手写 `("oauth", "oauth_token")` 字面量，改为 import
`narranexus.platform.schema.provider_schema.SUBSCRIPTION_AUTH_TYPES`（唯一定义）。行为不变；将来增删订阅
运输层时这里自动跟随。全仓扫描守卫：`tests/agent_framework/test_claude_fanout_concurrency.py::test_no_consumer_spells_the_subscription_set_by_hand`
（扫 `src/`、`backend/`、`plugins/*/src`；前端手抄件 `lib/agentFramework.ts` 不在射程内）。

## 2026-08-27(review 第 5 轮)— derive_auth_ref 签名改 (source, auth_type) 无默认值

source 守卫落地后,省略 source 只会返回 None——这个默认值不存在正确
用法,且参数序与兄弟函数(`derive_driver_type(source, auth_type,
protocol)` / `derive_billing_policy(source, auth_type)`)相反,按位置
并排调用时写反不报错、只产出空 auth_ref(P1 零信号复现)。现签名
`(source, auth_type)`、无默认,漏传即 TypeError。**None 与 "" 的区分
必须保留**:`from_row` 的 `or derive(...)`、`_cli_subscription_row_fields`
的 `or ""`、`build_codex_config` 的 `or (card.auth_ref or "")` 三处都
依赖 None 触发回落。真值表有直接的表驱动测试钉住
(`test_derive_auth_ref_truth_table`,断言 `is None` 非 falsy)。
2026-08-28(PR bot 轮):CLI 订阅源集合抽成模块常量
`CLI_SUBSCRIPTION_SOURCES`(进 `__all__`),derive_auth_ref 的守卫与
user_service `_cli_subscription_row_fields` 的 scope 守卫共用;加第三个
CLI framework 时集合与 per-source 分支都要扩,helper 的非 None 兜底会把
半成品扩展变成响亮的 ValueError 而非静默 driver_type=NULL 行。

## 2026-08-27 — derive_auth_ref 的 source 守卫收进函数本体(P1 review 第 2 轮)

旧真值表对任意 `auth=="oauth"` 的行默认发 **claude** 哨兵,靠调用方
(backfill 的 `if driver_type in {...}`)兜边界;`ProviderCard.from_row`
的读时推导落地后成了第二个调用方且没抄那道守卫——手工构造
`card_type=anthropic + auth_type="oauth"`(路由层 auth_type 是自由字符串)
的行会继承 claude 哨兵,**用宿主订阅凭证给一张自身无凭证的卡验绿**。
现在守卫在函数内:source 不在 {claude_oauth, codex_oauth} 一律 None,
全部调用方自动同口径,backfill 的外置守卫同 commit 删除。消费者清点
(review 第 4 轮补齐):插入时(user_service
`_cli_subscription_row_fields`)、读时(`ProviderCard.from_row`)、
backfill、codex helper 构造(cli_helper)、`build_codex_config` 强制
覆盖(后两者 source 传字面量 "codex_oauth",语境即 codex)。注意:
oauth_token / 非 oauth 仍返回 **None 而非 ""**——from_row 的
`row.get(...) or derive(...)` 与 user_service `_cli_subscription_row_fields`
的 `or ""` 都依赖这个区分。测试:
`test_from_row_does_not_derive_auth_ref_for_non_cli_sources`。

## 2026-07-26 — `derive_billing_policy`：`oauth_token` → `external_oauth`

setup-token 与 host-CLI oauth 同为订阅运输层，token 由 Anthropic 侧计费
——只记 cost_records，不扣 quota。注意 `derive_auth_ref` 对 oauth_token
返回 None 是有意的：token 行没有凭据文件 sentinel（token 本身就是凭据，
在 api_key 列），backfill 的 `auth_ref is not None` 守卫因此不会碰
token 行。

# derive.py — pure helpers

## Why these functions live outside Driver classes

* ``backfill`` runs them against raw DB rows that aren't yet
  classified — Driver instances don't exist there.
* ``self_heal`` needs the "is this slot broken / what's a safe
  default" decision before it has decided which Driver to use.
* Tests can table-drive them without DB fixtures.

Keeping them in a separate module enforces that the logic is pure —
no DB calls, no side effects, no provider lookups.

## derive_driver_type

The truth table sits in the docstring. Two gotchas:

* ``user`` source maps to ``custom_anthropic`` / ``custom_openai`` —
  the legacy ``source='user'`` ProviderSource enum is intentionally
  ambiguous about protocol, so we disambiguate here.
* ``system`` source maps to ``system_pool`` — the corresponding
  Driver is cloud-only. Backfill on a local DB will never see a
  ``system`` source row because there's no UI to create one.

## derive_billing_policy

Three values: ``user_pays`` (default), ``system_quota`` (cloud
system pool), ``external_oauth`` (Claude OAuth — Anthropic does the
billing on their side). ``cost_tracker`` reads ``billing_policy``
post-call to decide whether to deduct from ``user_quotas``.

## derive_auth_ref + credential path resolvers

OAuth rows store a sentinel string ``claude-cli:~/.claude/.credentials.json``
or ``codex-cli:~/.codex/auth.json`` in ``auth_ref`` depending on
provider source. The two recognised sources are exactly
``CLI_SUBSCRIPTION_SOURCES = {"claude_oauth", "codex_oauth"}``; anything else gets ``None``
(the guard lives inside the function, see the 2026-08-27 note). ``derive_driver_type`` maps
``source == "codex_oauth"`` to the driver_type of the same name, the twin of
``claude_oauth`` — the pair is what makes "one CLI framework owns one subscription card source"
mechanical rather than a per-call-site `if`. ``resolve_claude_credentials_path`` and
``resolve_codex_credentials_path`` expand those sentinels at use-time,
respecting the relevant override env vars so admins can relocate the
credentials file (or tests can inject a fake one).

## is_slot_broken + pick_default_model

The key business rule: the check is against the **card's own**
``models`` array, not against the global catalog. A user who configured
a private model that we don't recognise is fine; only a slot whose
model isn't in its own provider's list is broken.

**`SLOT_MODEL_SENTINELS` is the load-bearing exemption.** It holds `"default"`, the UI/config
convention meaning "use whatever the call site passed, or the catalog default" (see
`OpenAIAgentsSDK._resolve_model`). `is_slot_broken` returns False for a sentinel BEFORE the
membership test, and it must: self-heal would otherwise "repair" such a row by pinning the slot to a
concrete model, silently killing the per-call-site override the sentinel exists to allow — a
degradation with no error anywhere. Note the ordering also means an empty/None `slot_model` is still
broken (a mis-configured row), because the sentinel check is an exact-membership test, not a
truthiness one. Adding a second sentinel is a one-line change here and requires no edit to
self_heal.

``pick_default_model`` order: onboarding default for (source, slot kind) if on the card →
first element of card.models → ``model_catalog.get_default_models(source, protocol)[0]`` (empty card only) → ``None`` (caller logs and lets the call fail).
See the 2026-10-16 entry above for why the card head is no longer the first choice.
