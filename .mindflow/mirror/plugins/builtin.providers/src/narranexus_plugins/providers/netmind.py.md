---
code_file: plugins/builtin.providers/src/narranexus_plugins/providers/netmind.py
last_verified: 2026-09-07
stub: false
---
## 2026-09-03 — 末尾新增 `CONTRIBUTION`（插件平台批 0）

`CONTRIBUTION = <Driver>.contribution`：`@register` 生成的 `Contribution` 对象，被
`narranexus.kernel.plugins.builtins` 的 `builtin.providers` manifest 按符号名引用。行为不变。

## 2026-06-10 — build_anthropic_helper_config

Implements the new helper-slot builder for anthropic-protocol rows
(guarded the same way as build_claude_config). Lets this card serve
the helper_llm slot directly via the Messages-API helper.


# netmind.py — NetMind aggregator one-key card

Two ``user_providers`` rows per NetMind key — one
``protocol=anthropic`` (bearer_token, ``inference-api/anthropic``
endpoint), one ``protocol=openai`` (api_key, ``inference-api/openai/v1``
endpoint). They share a ``linked_group`` and the api_key.

The Driver instance picks the right config builder by checking the
card's own protocol. Mis-bindings (e.g. helper_llm pointing at the
anthropic row) raise loud NotImplementedError instead of silently
constructing a bogus config.

``supports_anthropic_server_tools=False`` is hardcoded — NetMind is
an aggregator, it doesn't forward Anthropic's server-side tools.
The tool-policy hook denies WebSearch upfront on this card so the
caller doesn't hang.

## 2026-09-07（round-2 G2-I5）— `CONTRIBUTION` → `CONTRIBUTIONS`

`model.providers` is a MANY-arity slot, so API_POLICY §8 wants the plural. The nine provider modules
were split between the two spellings for the same slot, which is exactly the drift §8 exists to stop.
Symbol name and manifest ref only; the registered driver name is unchanged.
