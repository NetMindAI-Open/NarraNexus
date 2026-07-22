---
code_file: src/xyz_agent_context/agent_framework/provider_driver/base.py
last_verified: 2026-07-20
stub: false
---
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
