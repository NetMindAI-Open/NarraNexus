---
code_file: frontend/src/lib/agentFramework.ts
last_verified: 2026-07-10
stub: false
---

## 2026-07-10 — 删 CODEX_ALLOWED_PROVIDER_SOURCES + curated 收窄到 codex_oauth

`CODEX_ALLOWED_PROVIDER_SOURCES` 常量**已删除**。它以前是 codex_cli agent slot 的
source 白名单(`{codex_oauth, user}`),配合后端 `validate_slot_binding` 把 NetMind /
Yunwu / OpenRouter 挡在外面。按铁律 #15(平台不替用户判断 provider 是否合适)整条
移除,恢复 pre-#81 行为——codex agent slot 现在只查 protocol。调用它的两处过滤
([[AgentLlmConfigPanel]] / ModelDefaultsSettings)同步删掉 source 分支,只留
`p.protocol !== fw.protocol`。

`getModelsForSlot` 的 codex 分支**收窄到 `prov.source === 'codex_oauth'`**:只有
OpenAI 自己的 codex 后端才强制 `CODEX_CURATED_MODELS`(它按账号 tier 网关);其他
openai provider(聚合商/自填 base_url)返回自己的 `prov.models`。与后端
`get_user_service.get_user_config`(同样 codex_oauth-only 覆盖)对齐。修掉"选了
netmind 却只看得到 gpt-5.x 三个模型"的 bug。详见后端 mirror
[[user_provider_service]] 2026-07-10 条目。

## 2026-07-09 — defaultHelperModel(选 helper provider 时默认便宜模型)

新增 `defaultHelperModel(source, protocol, modelIds)`:helper slot 选定 provider 后,
默认挑**推荐的便宜模型**而非 `models[0]`(旗舰)。优先 `RECOMMENDED_HELPER_MODEL_BY_PROTOCOL`
(openai→gpt-5.4-mini / anthropic→claude-haiku-4-5);OAuth provider 列的是 CLI 别名、
具体推荐 id 可能不在列表里,故映射到后端 auto-bind 同款别名(claude_oauth→`haiku`、
codex_oauth→`gpt-5.4-mini`),都不在时才回退首个。`ModelDefaultsSettings` 与
`AgentLlmConfigPanel` 共用,修掉"选 codex 后 helper 默认成 gpt-5.5 旗舰 / 选 claude 后默认
opus"的问题。

## 2026-07-09 — shared framework/model helpers for the provider UI

Single source of truth for the LLM provider/slot UI, shared by the user-level
Settings editor ([[ProviderSettings]]) and the per-agent chat surfaces
([[ComposerModelBadge]], [[AgentLlmConfigPanel]]) so a per-agent override offers
exactly the same choices as the global-default editor.

Holds: ``AGENT_FRAMEWORKS`` + ``isCodexFramework``; ``CODEX_CURATED_MODELS``
(codex_oauth-only — mirror of backend ``user_provider_service``);
``RECOMMENDED_HELPER_MODEL_BY_PROTOCOL`` (mirror of backend
``_ONBOARD_HELPER_MODELS``); ``MODEL_SUGGESTION_GROUPS``; reasoning option
lists; and ``getModelsForSlot(prov, slot, framework, knownModels)`` (agent+codex
**on codex_oauth** → curated set, every other provider → its own models). These
were previously local to ProviderSettings; extracting them avoided duplicating
the codex rules across the new per-agent components. (The old
``CODEX_ALLOWED_PROVIDER_SOURCES`` source-allowlist was removed 2026-07-10 — see
the dated entry above.)
