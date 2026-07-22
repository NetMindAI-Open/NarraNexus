---
code_file: frontend/src/components/chat/AgentLlmConfigPanel.tsx
last_verified: 2026-07-18
stub: false
---

## 2026-07-18 — 云端锁定 per-agent 框架（禁用 → alert → useConfirm，三改定稿）

与 [[ModelDefaultsSettings]] 同款演进，定稿为 `useConfirm().alert` 样式弹窗
（Tauri wry 不渲染 window.alert）：`netmindOnly` 下选到不同框架时弹
`cloudFrameworkLockedTitle`/`cloudFrameworkLocked` 提示并手动
`e.target.value` 弹回（受控 select state 未变不会重渲染）。组件返回值包成
fragment：`<>{主 Dialog}{noticeDialog}</>`（两个 portal 同 z-1000，后挂载者
在上）。后端侧该锁已同日补齐：`set_agent_slot` 的框架钉选门禁
（[[agent_slot_service]] 2026-07-18）会 403 与 owner 默认不同的钉选——前端
弹窗只是把这个 403 变成事前解释。

## 2026-07-17 — 云端 netmind-only：下拉过滤 + "下载本地版"提示

与 [[ModelDefaultsSettings]] 同日同款：`netmindOnly =
cloudNetmindOnly(configStore.role)` 为真时两个 provider 下拉只留
netmind-source 卡，error 行上方多一段同 i18n 键的提示 + DESKTOP_RELEASES_URL
链接。两个面板必须一起限，否则 per-agent 覆盖就是 Model Defaults 限制的绕行
通道（后端 agents_llm_config 门禁是最终防线，这里只是让 UI 不出 403）。

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
