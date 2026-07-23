---
code_file: frontend/src/components/settings/ModelDefaultsSettings.tsx
last_verified: 2026-07-18
stub: false
---

## 2026-07-18 — 框架选择器弹窗方向化(修云端老 codex 死锁)

弹窗条件原为 `netmindOnly && e.target.value !== framework`——对**任何**切换都弹窗+回退,
包括切回 claude_code,前端也把老 codex 用户锁死。改为 `e.target.value !== 'claude_code'`:
切回 claude_code 放行,只在切到非 claude_code 时提示。与后端 providers.py 403 方向化一致
([[AgentLlmConfigPanel]] 同款)。

## 2026-07-18 — 云端框架锁：禁用 → alert → useConfirm 样式弹窗（三改定稿）

Owner 走查三轮：① `disabled` + 常驻提示（不友好）→ ② `window.alert`（生硬，
且 **Tauri wry 根本不渲染 window.alert** —— ConfirmDialog.tsx 存在的原因，
绝不能用原生弹窗）→ ③ 定稿：`useConfirm().alert`（与加服务商同一 Dialog
外壳），标题 `cloudFrameworkLockedTitle` + 正文 `cloudFrameworkLocked` +
DESKTOP_RELEASES_URL 下载链接，`{noticeDialog}` 挂在组件根部。**坑**：受控
select 的 state 未变 → React 不重渲染 → 必须手动 `e.target.value = framework`
弹回。API 不会被调用（guard 先 return）。背景：后端 `POST /agent-framework`
的云端 staff-gate 会 403 普通用户；Owner 定案保持 staff-only。
[[AgentLlmConfigPanel]] 同款（其弹窗不带下载链接，正文更短）。

## 2026-07-17 — 云端 netmind-only：下拉过滤 + 底部"下载本地版"提示

`netmindOnly = cloudNetmindOnly(configStore.role)`（[[agentFramework]] 新谓词，
云端非 staff 为真）。为真时 agent/helper 两个 provider 下拉都隐藏
`source !== 'netmind'` 的卡（后端 route 门禁会 403 它们，UI 不给出这个选项），
且 Save 行下方多一段 note：`pages.settings.modelDefaults.cloudNetmindOnlyNote`
（"云端版本使用你的 NetMind 账户运行——这里不能使用你自己 API key 的模型"）+
指向 `DESKTOP_RELEASES_URL` 的下载链接。本文件因此首次引入 useTranslation
（en/zh 键 + 内联默认，其余文案仍是硬编码英文）。本地/staff 完全不受影响。
测试：__tests__/ModelDefaultsSettings.test.tsx（cloud user / cloud staff /
local 三态）。

## 2026-07-10 — agent slot 去掉 codex source 过滤

`agentProviders` 过滤删掉 `isCodexFramework → CODEX_ALLOWED_PROVIDER_SOURCES`
分支(该常量已删),只留 protocol 检查。用户级默认编辑器与 per-agent 面板
([[AgentLlmConfigPanel]])共用同一规则,一起恢复 pre-#81:codex_cli 能选任意
openai-protocol provider(铁律 #15,见 [[user_provider_service]] /
[[agentFramework]])。`isCodexFramework` 仍用于 framework 切换的 spinner 文案,保留;
只删 `CODEX_ALLOWED_PROVIDER_SOURCES` import。

## 2026-07-09 — helper dropdown 放开 OAuth + 默认便宜模型

`helperProviders` 过滤去掉 `p.auth_type !== 'oauth'`——helper 现在可选 OAuth
(claude_oauth / codex_oauth),因为后端把 OAuth helper 路由成 CliHelperConfig 经同一 CLI
一次性跑(一个订阅覆盖两个 slot)。选定 provider 后的默认 model 改用
`defaultHelperModel`(见 [[agentFramework]]),挑便宜的 `gpt-5.4-mini`/`haiku` 而非旗舰
`models[0]`。提示文案同步改为"OAuth 也能用"。这是"OAuth 覆盖 helper"特性适配 #81 重构 UI
的前端部分(旧 `ProviderSettings.tsx` 的同款放开已随 #81 重构失效)。

## 2026-07-09 — global default model editor (Settings › Model Defaults)

The provider + model + coding-agent framework every agent INHERITS by default —
extracted out of [[ProviderSettings]]' old "Section ③" so LLM Providers is purely
the credential wallet. Rendered under the new "Model Defaults" nav item
([[SettingsPage]]).

Edits two user-level slots and writes via the unchanged endpoints:
`PUT /api/providers/slots/{agent|helper_llm}` (`api.setProviderSlot`) +
`POST /api/providers/agent-framework` (`api.setAgentFramework`). The framework
switch persists immediately (it may auto-install codex + re-probe auth) and
clears the agent provider/model on a protocol change; the two slots save
together on "Save defaults" (writes only the changed slots). Option-building is
shared via [[agentFramework]] (`getModelsForSlot` / `AGENT_FRAMEWORKS`) so the
choices match the per-agent panel ([[AgentLlmConfigPanel]]) and the provider
dropdowns. (The codex source whitelist was dropped 2026-07-10 — agent slot is
protocol-gated only.)

Structurally close to the per-agent [[AgentLlmConfigPanel]] (agent framework +
model + reasoning, helper model), but inline (not a modal) and writing the
user-level default instead of a per-agent override. Panel copy points users to
the chat page for per-agent overrides. Empty state when no providers exist:
prompts to add one under LLM Providers first.

Note: the cloud staff-gate on framework switching is enforced by the backend
(403 on `setAgentFramework`); this panel surfaces the error rather than
pre-fetching claude/codex status to render a read-only display (a lighter
version of the old renderSlotRow behavior).
