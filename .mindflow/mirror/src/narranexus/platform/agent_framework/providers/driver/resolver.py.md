---
code_file: src/narranexus/platform/agent_framework/providers/driver/resolver.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28（PR #418 review I2）— 运行配置构建前的出网筛

本函数按 provider_id **直接**读 `user_providers` 行（per-agent override 指向的卡也从这里进，
不经过 `UserProviderService.get_user_config`），所以 is_active 检查之后再调
[[registry]] 的 `is_stored_user_base_url_blocked`：云端、非平台 source（`user`/缺失/真正未知；
平台白名单 `PLATFORM_PROVIDER_SOURCES`）、base_url 明显非公网（DNS-free）→ 抛
`LLMConfigNotConfigured`，调用方已有的友好报错路径接住；文案**不回显 base_url**。平台卡
（`netmind_free`，以及枚举外的系统免费额度池 `system` / `system_pool`——本函数正是它们的必经之路）
与 local 不受影响。每个 slot 绑定只调一次，纯内存判定，不解析 DNS。

- **触发**：把「不在 `ProviderSource` 枚举里」当成 USER → **症状**：绑在 `system_pool` 卡上的云端用户
  每次 agent 运行 / helper 调用都 `LLMConfigNotConfigured`，`/health` 照绿 → **根因**：
  `user_providers.source` 的取值域大于枚举（PR#418 增量 review C1，已改为白名单）。
- 该 import 单独成组放在 `driver.*` 之前（review M11），别和 `driver.registry` 混看。

- **触发**：只在 `get_user_config` 加筛 → **症状**：user_slots 绑定的坏卡被拦，但某个 agent 的
  `agent_slots` override 指向同一张卡时 agent loop 照样向内网发请求 → **根因**：resolver
  classify 只看 user 级 slot，override 叠加只发生在本函数里。

## 2026-09-10（PR #394 review I3）— `_apply_agent_overrides` 用共享判据

覆盖判据改为 [[model_identity]] 的 `config_override_wins(row)`（非空 provider_id 即胜出，
行为不变）。它是全仓「per-agent 覆盖是否换掉 provider」的唯一定义，熔断器的收窄恢复、
[[model_health]]、[[slot_service.py]] 的计数/概览都读它，改规则只改一处。

## 2026-09-10 — 订阅集合改 import `SUBSCRIPTION_AUTH_TYPES`（PR#392 复审 I2）

`helper_llm` 槽走 CLI helper 的判定原先手写 `("oauth", "oauth_token")` 字面量，改为 import
`narranexus.platform.schema.provider_schema.SUBSCRIPTION_AUTH_TYPES`（唯一定义）。行为不变；将来增删订阅
运输层时这里自动跟随。全仓扫描守卫：`tests/agent_framework/test_claude_fanout_concurrency.py::test_no_consumer_spells_the_subscription_set_by_hand`
（扫 `src/`、`backend/`、`plugins/*/src`；前端手抄件 `lib/agentFramework.ts` 不在射程内）。

## 2026-08-10 — source captured at the credential decision point

Resolution records each chosen `ProviderCard.source` into the task ContextVar.
No later layer guesses free-tier use from URL, model, or legacy branch.

## 2026-07-29 — 协议无关框架:openai 卡也能进 agent 槽

`nexus_power` 自己驱动 provider API(不外挂 CLI),所以两族 provider 都能用。
新增 `_is_protocol_agnostic_framework`,`_resolve_slot_target` 对这类框架按卡
的协议分派:anthropic → `build_claude_config`,openai → `build_codex_config`
(agent 槽的 openai 载体是 codex,`openai_config` 属于 helper_llm,别混)。
同时把 `nexus_power` 补进 `_KNOWN_AGENT_FRAMEWORKS`——漏了这一条会**静默
降级成 claude_code**(实测踩到:槽里写着 nexus_power,跑的却是 claude)。

## 2026-07-26 — helper 槽分派：`oauth_token` 与 `oauth` 同走 CLI helper

`_resolve_slot_target` 的订阅判断从 `== "oauth"` 扩为
`in ("oauth", "oauth_token")`——setup-token 卡同样不能打 raw API，helper
的结构化一次性调用与 agent 槽共用同一 claude CLI 运输层（一个 token 覆盖
双槽）。

## 2026-07-18 — 清掉陈年"migration window"注释（review 抓出）

模块 docstring 声称"仍需为 prefer_system_override 分支加载 quota 行"——
grep 证实本模块从不读 quota 行或该列（迁移窗口早已结束，注释一直没删）。
已改写为如实描述：quota 门禁在 [[resolver]]，该列自 2026-07-18
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
``providers.user_service._SUPPORTED_AGENT_FRAMEWORKS``. If a slot row
carries an unknown framework name, ``_agent_framework_from_slot``
falls back to ``"nexus_power"`` (the platform default since 2026-08-20; claude_code was the historical one) rather than
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
              ├─ get_driver_class(driver_type) → Driver instance  (driver_registry(), not a constant)
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

## 2026-09-07 — 框架白名单换注册表（B6）

_KNOWN_AGENT_FRAMEWORKS/_is_codex_framework/_is_protocol_agnostic_framework 删除。_agent_framework_from_slot 用 resolve_framework_name（空→bound 默认）+framework_meta 校验，未注册的名字抛 LLMConfigNotConfigured 而不是静默换成 nexus_power（设置页写 Claude Code 却跑默认框架正是要拒绝的替换）。_resolve_slot_target 按 FrameworkMeta.protocol 选配置形状：openai→build_codex_config，any→按卡片协议，anthropic→claude 配置——第三方框架无需改 resolver。

## 2026-09-07（round-2 T2-C6）— the pipeline reads `driver_registry()`, not a `DRIVER_REGISTRY` constant

The diagram above named a symbol that no longer exists anywhere in the repo. Lookup goes through
`registry.get_driver_class(driver_type)`, which resolves the kernel's `model.providers` registry at
call time; `None` means "nobody provides that driver_type in THIS process" (unbooted platform, or a
distribution that excludes `builtin.providers`) and the resolver raises `LLMConfigNotConfigured`
rather than routing to a default that would bill the wrong account.
