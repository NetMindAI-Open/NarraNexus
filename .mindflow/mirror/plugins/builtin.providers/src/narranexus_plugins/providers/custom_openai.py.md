---
code_file: plugins/builtin.providers/src/narranexus_plugins/providers/custom_openai.py
last_verified: 2026-09-07
stub: false
---

# custom_openai.py — user-configured OpenAI provider

Anything ``source='user'`` + ``protocol='openai'``. Serves both
helper_llm and embedding slots from the same card row.

OpenAIConfig + EmbeddingConfig both get the card's ``api_key`` and
``base_url``. Empty base_url means "use OpenAI official defaults" —
the OpenAI SDK fills it in.

## 2026-09-03 — 末尾新增 `CONTRIBUTION`（插件平台批 0）

`CONTRIBUTION = <Driver>.contribution`：`@register` 生成的 `Contribution` 对象，被
`narranexus.kernel.plugins.builtins` 的 `builtin.providers` manifest 按符号名引用。行为不变。

## 2026-09-07（round-2 G2-I5）— `CONTRIBUTION` → `CONTRIBUTIONS`

`model.providers` is a MANY-arity slot, so API_POLICY §8 wants the plural. The nine provider modules
were split between the two spellings for the same slot, which is exactly the drift §8 exists to stop.
Symbol name and manifest ref only; the registered driver name is unchanged.
