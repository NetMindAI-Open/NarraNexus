---
code_file: frontend/src/components/chat/AgentLlmConfigPanel.tsx
last_verified: 2026-07-10
stub: false
---

## 2026-07-10 — agent slot 去掉 codex source 过滤

`agentProviders` 过滤删掉 `isCodexFramework → CODEX_ALLOWED_PROVIDER_SOURCES`
分支,只留 `p.protocol !== fw.protocol`。codex_cli 现在能选任意 openai-protocol
provider(含 netmind/yunwu/openrouter),恢复 pre-#81。理由与后端一致(铁律 #15,
见 [[user_provider_service]] / [[agentFramework]] 2026-07-10)。相应删掉
`isCodexFramework` / `CODEX_ALLOWED_PROVIDER_SOURCES` 两个 import。

## 2026-07-09 — per-agent helper 放开 OAuth + 默认便宜模型

与 [[ModelDefaultsSettings]] 同步(per-agent 覆盖面板):`helperProviders` 去掉
`p.auth_type !== 'oauth'` 过滤,helper 可选 OAuth;选定 provider 后默认 model 用
`defaultHelperModel`(见 [[agentFramework]])挑便宜的 mini/haiku 而非旗舰;提示文案改为
"OAuth 也能用"。后端 `validate_slot_binding` 已一致放开 helper 的 OAuth(见
[[user_provider_service]]),所以 per-agent override 也能绑 OAuth helper。

## 2026-07-09 — per-agent LLM config modal

Detailed per-agent editor opened from the "Model & framework settings…" link in
[[ComposerModelBadge]]'s dropdown. Edits both slots for ONE agent: agent
(framework + provider + model + thinking + reasoning_effort) and helper_llm
(provider + model). Each slot shows "inheriting default" vs "custom for this
agent".

**ONE Save button** (footer) applies the whole panel — but writes ONLY the slots
the user changed (diff of draft vs the snapshot taken on load), so editing the
agent model never silently turns an inheriting helper into a custom override. A
per-slot "Reset to the global default" link (DELETE the override) shows only
when that slot is currently custom. Writes via [[api]]'s setAgentLlmConfig /
resetAgentLlmConfig; changes apply on the agent's NEXT run (no hot-reload —
config is resolved per run from the DB).

Provider filtering mirrors the backend binding rules (via [[agentFramework]]):
agent slot follows the framework protocol ONLY (no source whitelist since
2026-07-10); helper slot is openai/anthropic and now ALSO accepts OAuth
providers (backend routes them via CliHelperConfig). Modeled on AwarenessPanel's
per-agent modal. Uses getProviders() for the
option lists; display names fall back to raw model ids (no catalog dependency).
