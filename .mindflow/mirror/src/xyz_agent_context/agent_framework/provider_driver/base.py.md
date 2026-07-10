---
code_file: src/xyz_agent_context/agent_framework/provider_driver/base.py
last_verified: 2026-07-07
stub: false
---
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
   ``on_call_completed``, NotImplementedError stubs for the three
   ``build_*_config`` methods) so concrete drivers stay tiny.

## CallContext

Drop-in info for the post-call hook. Only ``SystemDriver`` actually
reads it; other drivers ignore the argument. Kept frozen so it can't
be mutated mid-flight between cost_tracker layers.

## DriverHealth

Output of ``probe()``. Three fields, all optional except ``ok``.
``expires_at`` is here for the OAuth driver's TTL surfacing — other
drivers leave it as ``None``.

## 2026-07-07 — build_cli_helper_config

Driver Protocol + `_DriverBase` 新增 `build_cli_helper_config`（默认 NotImplementedError）。仅 OAuth driver 覆盖它，为 helper 槽产出 `CliHelperConfig`（订阅同时覆盖两槽）。
