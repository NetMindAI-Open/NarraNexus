---
code_file: src/xyz_agent_context/agent_framework/provider_driver/resolver.py
last_verified: 2026-07-18
stub: false
---

## 2026-07-18 — 清掉陈年"migration window"注释（review 抓出）

模块 docstring 声称"仍需为 prefer_system_override 分支加载 quota 行"——
grep 证实本模块从不读 quota 行或该列（迁移窗口早已结束，注释一直没删）。
已改写为如实描述：quota 门禁在 [[provider_resolver]]，该列自 2026-07-18
起仅是通知闩锁。

## 2026-07-09 — per-agent slot overlay (agent_id)

``resolve_user_runtime_llm_configs`` gained ``agent_id: str | None``. After
building ``by_slot_name`` from ``user_slots`` it calls ``_apply_agent_overrides``:
load ``agent_slots`` rows for the agent and, for each row whose ``slot_name`` ∈
``_REQUIRED_SLOTS`` with a non-empty ``provider_id``, overlay it. Both agent and
helper slots can be overridden; empty-provider stub rows are skipped (a
framework-only stub must not shadow the user default — matches
``step_3._resolve_agent_framework_name``). No ``agent_id`` / no override rows →
byte-identical to the user-only path. The overlaid row carries ``agent_id`` (not
``user_id``), which is how [[self_heal]] routes its writeback to ``agent_slots``.

## 2026-06-17 — 把 codex / helper 派发收进 driver 多态(铁律 #9)

去掉 resolve loop 里两处 `if` 特判,改成单一决策点 `_resolve_slot_target(
slot, framework, card) → (driver 方法名, cfgs key)`,loop body 变成统一的
`getattr(driver, method)(...)`:

- **codex 不再走 resolver 里的 free function**。原 `_codex_config_from_card`
  已删除,逻辑下沉到 driver:`_DriverBase.build_codex_config`(任意 openai 协议
  卡的通用 api-key 路径,非 openai 抛 NotImplementedError)+ `CodexOAuthDriver`
  override(强制 `CODEX_CLI_CREDENTIALS_REF`)。codex_oauth 的凭证 ref 特例现在
  住在 codex_oauth driver 里,而不是 resolver 的 `if source=="codex_oauth"`。
- **helper anthropic 派发**也并入这一个决策点(原 `if protocol=="anthropic"`)。
- `_SLOT_BUILDERS` dict(值已无用)换成 `_REQUIRED_SLOTS` tuple。
- 末尾组装统一用 `cfgs.get(key) or default`:codex agent 时 `claude` 留空默认,
  anthropic helper 时 `openai` 留空默认。
- 加协议(如 Gemini)从此只需教 `_resolve_slot_target` + 写一个 driver,
  不再编辑 loop body / 不再有第二份手抄(api_config legacy fallback 已删,见
  该文件 2026-06-17 条目)。回归:`test_codex_oauth_driver.py` 新增
  build_codex_config 三例;103 个 provider/resolver 测试全绿。

## 2026-06-10 — helper protocol dispatch + slot reasoning params threaded

Two changes to the slot loop: (1) helper_llm now branches on the card's
protocol — anthropic cards build via `build_anthropic_helper_config` into
`RuntimeLLMConfigs.anthropic_helper` (`.openai` stays an empty default; the
helper factory dispatches off `anthropic_helper` being set). (2) the agent
slot's `params_json` (neutral thinking/reasoning_effort) is parsed by
`_slot_reasoning_params` and threaded into BOTH agent paths —
`dataclasses.replace` onto the built ClaudeConfig, and new kwargs on
`_codex_config_from_card`. Previously only the legacy fallback honored the
slot params; the driver path silently dropped them for Claude too.


# resolver.py — single-point LLM config resolution

## Why this exists

The codebase had two parallel resolve paths — ``ProviderResolver`` for
HTTP middleware and ``api_config._get_user_llm_configs_strict`` for
background triggers. Each had its own completeness check and error
handling, drift between them caused real bugs (Xiong's case shipped
because the frontend Settings check ran the HTTP path while the LarkTrigger
that broke ran the background path). This module collapses both into one
function and ``api_config._get_user_llm_configs_strict`` now delegates
to it. ``ProviderResolver.resolve_and_set`` will follow in Phase 2.

As of 2026-05-31, the runtime path also resolves Codex agent config.
When the agent slot row has ``agent_framework == "codex_cli"``, the
agent slot is interpreted as an OpenAI-protocol Codex provider and
produces ``CodexConfig`` in the returned ``RuntimeLLMConfigs`` bundle.
The legacy ``resolve_user_llm_configs`` wrapper still exposes the old
three-config tuple for non-agent-loop callers.

**Single canonical framework names** (2026-06-08 cleanup): the
``_KNOWN_AGENT_FRAMEWORKS`` whitelist is just
``("claude_code", "codex_cli")`` — the A/B period briefly listed
``codex_cli_v2`` / ``codex_official`` but those were dropped after
cutover (binding rule #2). ``_is_codex_framework`` is now just
``framework == "codex_cli"`` wrapped in a helper for future-proofing.

This whitelist **must** stay in sync with
``agent_framework/__init__.py`` registrations and with
``user_provider_service._SUPPORTED_AGENT_FRAMEWORKS``. If a slot row
carries an unknown framework name, ``_agent_framework_from_slot``
falls back to ``"claude_code"`` (the historical default) rather than
let an unrecognised value pass through silently — typo-resistance at
the resolver boundary.

Codex OAuth rows are canonicalized at resolve time: any
``source='codex_oauth'`` / ``auth_type='oauth'`` card uses the Codex CLI
auth reference, even if stale local data still carries a Claude CLI
``auth_ref`` from an older build. This keeps agent-loop auth tied to
``~/.codex/auth.json`` without requiring users to recreate the provider.

## Pipeline

```text
user_id
  └─ db.get('user_slots', user_id=user_id)  → 3 rows expected
       └─ for each slot:
            db.get_one('user_providers', provider_id=...)
              ├─ visibility check (owner_user_id matches OR is null)
              ├─ self_heal_if_broken (rewrites slot.model if needed)
              ├─ on-the-fly driver_type derive if backfill hasn't run yet
              ├─ DRIVER_REGISTRY[driver_type] → Driver instance
              └─ driver.build_<kind>_config(slot.model)
                 OR CodexConfig for codex_cli / codex_cli_v2 / codex_official
```

## Visibility rule

A card is visible if ``owner_user_id == user_id`` OR
``owner_user_id IS NULL``. The null case covers two things at once:

* Cloud system-shared cards (admin created the row with null owner).
* Legacy rows that pre-date the Phase 0 ``owner_user_id`` column —
  for those we fall back to ``card.user_id == user_id`` so the
  resolver doesn't refuse old data on first boot.

## Errors

Every failure raises ``LLMConfigNotConfigured`` with an actionable
message. The caller's UX layer surfaces it to the user. No silent
fallback to a different account — that was a leading cause of
billing surprises in the old code.

## 2026-07-07 — helper_llm 槽的 OAuth 走 CLI helper

`_resolve_slot_target` 的 helper_llm 分支：`auth_type==oauth` 的 card 先于 protocol 判断，路由到 `build_cli_helper_config` → cfgs 键 `cli_helper` → 装进 `RuntimeLLMConfigs.cli_helper`。
