---
code_file: plugins/builtin.providers/src/narranexus_plugins/providers/yunwu.py
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


# yunwu.py — Yunwu aggregator one-key card

Same dual-row pattern as ``netmind.py``: one anthropic row + one
openai row sharing a ``linked_group``. Differs only in base_url and
auth_type values (Yunwu uses api_key for both protocols, unlike
NetMind's bearer_token-for-anthropic quirk).

Aggregator semantics: ``supports_anthropic_server_tools=False``.

## 2026-09-07（round-2 G2-I5）— `CONTRIBUTION` → `CONTRIBUTIONS`

`model.providers` is a MANY-arity slot, so API_POLICY §8 wants the plural. The nine provider modules
were split between the two spellings for the same slot, which is exactly the drift §8 exists to stop.
Symbol name and manifest ref only; the registered driver name is unchanged.
