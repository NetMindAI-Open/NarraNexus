---
code_file: src/narranexus/platform/agent_framework/providers/driver/base.py
last_verified: 2026-09-04
stub: false
---

## 2026-08-27 — from_row 读时推导 auth_ref(P1 review 轮)

存量 host-CLI OAuth 行在启动 backfill 跑过之前 `auth_ref=NULL`(P1 工单
那批用户),而这一列对 claude/codex OAuth 是 `(auth_type, source)` 100%
可推导的(`derive_auth_ref`)。`from_row` 现在
`row.get("auth_ref") or derive_auth_ref(...)`——读时兜底,让这些行的
Test 不必等重启;**持久化仍归 backfill,别因为这行就删它**。与
`test_provider` 对兄弟列 `driver_type` 的读时推导同一哲学。守住的边界:
`derive_auth_ref` 对 `oauth_token` **和非 CLI 订阅 source**(review 第 2
轮加的守卫,见 derive.py.md)都返回 None——token 行不得被塞进文件哨兵
分支(token 本身即凭据),自由字符串 auth_type 的杂牌行不得继承宿主
claude 凭证,各有测试钉住
(`test_from_row_does_not_derive_auth_ref_for_token_rows` /
`test_from_row_does_not_derive_auth_ref_for_non_cli_sources`)。依赖方向:
新增 `base → derive` 单向 import(derive 纯 stdlib,无环)。同批:
`_DriverBase.probe()` 默认文案 "no api_key or auth_ref" 改
"no credential configured for this provider"(内部列名不进用户文案;
今日两个 OAuth driver 都有 verify_live,此分支暂无用户可达路径,纯防
下一个不带 verify_live 的 driver 复活 P1)。

## 2026-07-31 — VerifyVerdict 三态(PR #224 review 第 4 条)

`verify_live` 的返回从 bool 改为 `Literal["ok","dead","unknown"]`
(常量 VERIFY_OK/DEAD/UNKNOWN)。动机:把「确证已死」和「本节点无法
判定」压成同一个 False,会让"容器里没装 CLI / 控制面≠执行面 / 超时"
统统读成"凭证已死",而 False 经 [[user_service]] → [[readiness]] 会
永久拦死 PAUSED_NO_QUOTA job 的唯一边缘恢复入口。语义:只有 dead 可以
阻塞;unknown 必须不阻塞(消费方映射为 True 加 "(not live-verified)"
标注)。
## 2026-07-20 — 删除 on_call_completed 与 CallContext（死代码，行为不变）

Protocol 上的 `on_call_completed` 声明、`_DriverBase` 的空默认实现、以及只
服务于它的 `CallContext` dataclass（连同 `__all__` 与包导出）一并删除。

原因：全仓没有任何 dispatcher 调用过这个钩子，它是 Phase 1 的占位设计；真正
的免费额度扣减一直在 `utils/cost_tracker.py` 的 `record_cost` 里，依据
`provider_source` 上下文标签。唯一 override 它的 `SystemDriver` 在自己的
docstring 里声称"扣费由此完成"，与运行时事实不符 —— **文档描述的架构 ≠ 实际
运行的架构**是最容易误导后人的一类债。

删而不是仅改注释：留着它等于埋一颗会花钱的雷 —— 一旦有人给所有 driver 统一接
上这个钩子，`SystemDriver` 就会与 cost_tracker 的钩子双重扣费。模块 docstring
里已写明这条约束。详见 [[system]] 与 2026-07-20 的配额审计。
## 2026-06-17 — Driver grows build_codex_config(codex 进多态,铁律 #9)

第四个 build 方法 `build_codex_config(model, *, thinking, reasoning_effort)`
加到 Driver Protocol + `_DriverBase`。和前三个不同:`_DriverBase` 给的是**真实
现 default**(不是抛 NotImplementedError)——因为 codex 是"openai 协议卡上的一
种模式",不是某个 driver 独占的协议,所以任意 openai 卡都能用这个通用 api-key
路径;非 openai 卡才抛 NotImplementedError。`CodexOAuthDriver` override 它注入
共享 CLI 凭证 ref。这样 resolver 不再用 free function 特判 codex(见
resolver.py.md 2026-06-17)。

## 2026-06-10 — Driver grows build_anthropic_helper_config

Third build method on the Driver protocol + `_DriverBase` default
(NotImplementedError): `build_anthropic_helper_config(model)` →
`AnthropicHelperConfig` for the helper_llm slot on anthropic-protocol cards.
Implemented by custom_anthropic / netmind / yunwu / openrouter / system
(guarded by their `_is_anthropic_row()` predicate); OAuth drivers keep the
default — OAuth rows can't serve direct Messages-API calls.


# base.py — ProviderCard + Driver Protocol

## ProviderCard

In-memory snapshot of one ``user_providers`` row. Frozen dataclass so a
Driver instance can hand it around without anyone accidentally mutating
the source. ``from_row`` does the heavy lifting on JSON-text ``models``
and tolerates legacy rows where the Phase-0 columns are still null
(self-heal / resolver fall through to fallback paths in that case).

## Driver Protocol

``typing.Protocol`` instead of an ABC. Three reasons:
1. Future third-party drivers can duck-type without inheriting from us.
2. Tests can construct stub drivers without forwarding ``__init__``.
3. ``_DriverBase`` carries shared defaults (``models``, ``probe``,
   NotImplementedError stubs for the three ``build_*_config`` methods)
   so concrete drivers stay tiny.

## 驱动不计费

Driver 只构造凭证。免费额度扣减在 `utils/cost_tracker.py` 的 `record_cost`，
不在驱动层 —— 不要在这里加 per-driver 的 post-call 计费钩子，除非同时摘掉
cost_tracker 的 deduct（否则双重扣费）。历史上的 `on_call_completed` /
`CallContext` 就是这样一个从未接线的钩子，已于 2026-07-20 删除。

## DriverHealth

Output of ``probe()``. Three fields, all optional except ``ok``.
``expires_at`` is here for the OAuth driver's TTL surfacing — other
drivers leave it as ``None``.

## 2026-07-07 — build_cli_helper_config

Driver Protocol + `_DriverBase` 新增 `build_cli_helper_config`（默认 NotImplementedError）。仅 OAuth driver 覆盖它，为 helper 槽产出 `CliHelperConfig`（订阅同时覆盖两槽）。

## 2026-09-04 · `contribution` declared (batch 6b.2)

`_DriverBase.contribution` is a declared ClassVar (set by `@register`), so the driver modules — now the `builtin.providers` plugin package under pyright's scope — type-check.
