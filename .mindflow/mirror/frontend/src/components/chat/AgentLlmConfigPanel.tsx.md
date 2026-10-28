---
code_file: frontend/src/components/chat/AgentLlmConfigPanel.tsx
last_verified: 2026-10-16
stub: false
---

## 2026-10-16 (PR#413 round 2: N1/N4) — 默认表进 React state,取不到就报加载失败

`load()` 解构 `refreshOnboardDefaults()` 的 `{ok, defaults}`:成功则整体 `setOnboardDefaults`;`ok:false` 则 `setError(t('…loadFailed'))`(同 `fwRes?.success` 为假的既有做法,无新 i18n key)。
选 provider / 推荐提示把 `onboardDefaults` 显式传给 `defaultAgentModel` / `defaultHelperModel` / `recommendedHelperModel`。测试覆盖失败路径(报错可见)与成功路径(NetMind 选 V4.1-Flash 而非 omni 首个)。

## 2026-10-16 (复审 M2 回归修正) — 推荐提示不再回落列表首个

`helperRecModel` 走 `recommendedHelperModel`,只点名刻意选择(source 表/协议表/OAuth 别名在卡上),否则显示协议表模型;与"选 provider 后自动选"在无刻意选择时有意分叉(自动选回落首个)。
上一条"与自动选一致"的措辞作废,见 [[agentFramework]]。

## 2026-10-16 (PR#413 review I2+M2) — load 时刷新服务端默认表;推荐提示复用自动选

`load()` 与 getProviders/getAgentFramework 并行调用 `refreshOnboardDefaults()`(永不 reject,失败回落列表首个),保证渲染与 provider 切换前表已就位。
`helperRecModel` 现用该 helper provider 的模型列表调 `recommendedHelperModel`,与自动选一致。

## 2026-10-16 (预审修订) — helper "推荐"提示走 recommendedHelperModel

`helperRecModel` 不再只查协议表,改调 `recommendedHelperModel(source, protocol)`,与自动选模型一致(见 [[agentFramework]])。

## 2026-10-16 — agent provider 切换默认模型走 defaultAgentModel

切换 agent provider 时默认模型不再取 `models[0]`,改用 `defaultAgentModel(source, ids)`(NetMind 取 V4.1-Flash;见 [[agentFramework]])。

## 2026-09-11 — thinking / reasoning effort need a provider

Same rule as Settings → Model Defaults: the thinking and reasoning-effort
selects are disabled while the agent draft has no provider, and their labels
are bound with `htmlFor` (ids from `useId`). Before, they were editable on an
unbound slot and the save was refused with a provider/model message.

## 2026-09-11 — helper failure after the agent slot saved says so

Same class as ModelDefaultsSettings' partial save: `saveAll` writes the agent
slot (framework included, one call) then the helper. A helper failure after the
agent write used to show only the helper error with the agent still "dirty".
Now `load(helperDraft)` reloads the agent half as saved, keeps the unsaved
helper edit, calls `onSaved`, and shows `agentSavedHelperFailed`; a helper-only
failure is the plain error. Pinned by
`__tests__/AgentLlmConfigPanel.saveFeedback.test.tsx`.

## 2026-09-11 — a soft-failed framework load is reported

`GET /agent-framework` answering `success:false` (no throw) left no framework
list and no user framework: the framework select rendered with zero options
and no explanation (the provider list is empty too, since
`providerBacksFramework` fails closed without the list). `load()` now sets the
`loadFailed` error in that case. Pinned by
`__tests__/AgentLlmConfigPanel.defaultFramework.test.tsx` (soft failure →
error; normal load → none).

## 2026-09-11 — no hardcoded framework fallback

`EMPTY_DRAFT.agent_framework` is `''` and, when the agent has no owner slot
bound, the draft's framework falls back to the backend-resolved user framework
(`getAgentFramework().data.framework`, which already skips uninstalled plugins)
instead of the literal `'nexus_power'`. The helper draft's fallback is `''`
(the helper save never sends a framework). Pinned by
`__tests__/AgentLlmConfigPanel.defaultFramework.test.tsx`.

## 2026-09-11 — orphaned free-tier banner removed; "✓ Saved" after Save

- The 2026-07-23 free-tier banner below is gone. The backend stopped
  sending `data.free_tier` on 2026-07-28 (see [[agents_llm_config]];
  `tests/backend/test_agents_llm_config_routes.py` asserts it is absent)
  and the `chat.model.freeTierBanner` key was deleted from every locale
  then, but this panel kept the `freeTier` state and the banner JSX. The
  branch was unreachable, and had it ever rendered it would have shown a
  raw key. The `free_tier` field is also dropped from
  `api.getAgentLlmConfig`'s response type.
- The "✓ Saved" indicator now sits after the Save button, the same
  position ModelDefaultsSettings uses, so it no longer shifts Save when
  it appears. The 2.5s auto-clear comes from the shared `useFlashFlag`
  hook, the same one ModelDefaultsSettings uses: a second save within
  2.5s restarts the countdown (the earlier timer no longer cuts the newer
  confirmation short) and the pending timer is cleared on unmount.

## 2026-09-09 — a successful Save now says so (GitHub #96)

`saveAll`/`resetSlot` always reloaded silently on success — the dialog
stays open by design (one Save button can write both slots, and closing
after every partial edit would fight that), but nothing ever told the
user a save actually landed. A failed save already rendered `error`;
only the success path was mute, so a working Save looked identical to a
click that did nothing. Added a `saved` flag (mirrors
ModelDefaultsSettings' own "✓ Saved" indicator — same key
`pages.settings.modelDefaults.saved`, same 2.5s auto-clear, same
`saved && !isDirty` guard so it never coexists with a freshly-dirtied
draft). Test: `__tests__/AgentLlmConfigPanel.saveFeedback.test.tsx`
covers both the success confirmation and that a failure still shows the
error instead of a false "Saved".

## 2026-09-07 — new `liveFrameworks` state feeds `providerBacksFramework`/`availableFrameworks` (B6, same change as ModelDefaultsSettings)

Same fix as `ModelDefaultsSettings.tsx`'s 2026-09-07 entry (see there for the full rationale):
`lib/agentFramework.ts`'s matching functions now fail closed with no live `frameworks[]` list, so
`load()`'s already-fetched `fwRes.data.frameworks` is kept in a new `liveFrameworks` state
(`LiveFrameworkEntry[] | undefined`) instead of only feeding `frameworkAvailabilityMap`, and
passed as the third argument to both `providerBacksFramework` (`agentProviders`) and
`availableFrameworks` (`frameworkOptions`).

## 2026-08-28 — 框架下拉套插件可用性（与 ModelDefaultsSettings 同批）

`load()` 新增第三个并行请求 `api.getAgentFramework()`——插件安装态是
账户级事实、不是 per-agent 的，但 `frameworks` 字段目前只有这一个端点报，所以
每次打开面板都顺带拉一次，存进 `frameworkAvailability`。`frameworkOptions`
在 `availableFrameworks()` 之上套 [[agentFramework]] 的
`withFrameworkAvailability()`；`<option disabled>` + onChange 前置的插件门禁
与 [[ModelDefaultsSettings]] 同一套逻辑，弹窗里的链接调
`onClose(); navigate('/app/settings?tab=plugins')`——这里是模态框，用既有
"管理服务商"链接同款的关闭+跳转,而不是像 ModelDefaultsSettings 那样切
父级 nav state（这个面板不在 SettingsPage 内，没有那个 setState 可用）。

## 2026-07-31 — 框架下拉按钱包过滤（与 ModelDefaultsSettings 同批）

与 [[ModelDefaultsSettings]] 同一处改动：框架下拉走
`availableFrameworks(bindableProviders, agentDraft.agent_framework)`，
provider 下拉走 `providerBacksFramework`。本面板切框架仍然清本地草稿——
per-agent 覆盖只在 Save 时落库，没有"已持久化的非法组合"这个问题。

## 2026-07-29 — 框架门禁改问谓词，NexusPower 云端放行

与 [[ModelDefaultsSettings]] 同一处改动：拦截条件改成
`!frameworkAllowedInCloud(e.target.value, role)`。**下面 2026-07-18 那段的
`e.target.value !== 'claude_code'` 判据已被取代**——它把 NexusPower 一起拒了。

两个选择器必须同口径且都问 [[agentFramework]] 的同一个谓词：规则此前在这里、
在 [[ModelDefaultsSettings]]、在后端路由各自内联了一遍，NexusPower 变成云端
合法之后三处都还在拒。文案同步改为「Staff only in cloud」。

## 2026-07-29 — provider 过滤改问「框架收不收这个协议」

原来按 `framework.protocol` 单值过滤 provider 下拉。NexusPower 直接驱动
provider API，anthropic / openai 两族都能用，被单值过滤会砍掉一半合法选项。
改走 [[agentFramework]] 的 `frameworkAcceptsProtocol()`——CLI 型框架依旧只认
自己 CLI 的那一种，行为不变。

## 2026-07-23 — 免费额度生效诚实 banner (removed 2026-09-11, see top)

`load()` 读 `getAgentLlmConfig` 返回的 `data.free_tier`（[[api]]），`active` 为真时
面板顶部渲染一条信息 banner（`chat.model.freeTierBanner`，插值当前系统模型名）：说明
免费额度生效中、当前实际使用的模型、此处设置将在额度用尽后生效。**控件仍保持可编辑**
——刻意不硬禁用，让用户能预配置模型/框架，额度耗尽即自动生效；banner 负责诚实告知。
与底部 [[ComposerModelBadge]] 的只读锁定 chip 是同一根因的两个入口（云端免费额度优先
抢占 per-agent override，见 [[resolver]] / [[agents_llm_config]]）——徽章是
快捷切换故锁死，本详细面板允许预配置故只提示。

## 2026-07-18 — per-agent 框架选择器弹窗方向化(修云端老 codex 死锁)

同 [[ModelDefaultsSettings]]:弹窗条件从"任何切换都拦"改为只在切到非 claude_code 时拦
(`e.target.value !== 'claude_code'`),切回 claude_code 放行——让被锁的老 codex 用户能在
聊天页 UI 自救。与后端 403 方向化一致。

## 2026-07-21 — complete model-panel localization

All user-visible panel copy now reuses the Model Defaults i18n namespace:
headings, inheritance state, framework/provider/model selectors, reasoning
controls, helper recommendation, validation errors, reset actions, and footer
buttons. This keeps the per-agent override editor aligned with the global
editor in every locale without changing any stored configuration values.

## 2026-07-18 — 云端锁定 per-agent 框架（禁用 → alert → useConfirm，三改定稿）

与 [[ModelDefaultsSettings]] 同款演进，定稿为 `useConfirm().alert` 样式弹窗
（Tauri wry 不渲染 window.alert）：`netmindOnly` 下选到不同框架时弹
`cloudFrameworkLockedTitle`/`cloudFrameworkLocked` 提示并手动
`e.target.value` 弹回（受控 select state 未变不会重渲染）。组件返回值包成
fragment：`<>{主 Dialog}{noticeDialog}</>`（两个 portal 同 z-1000，后挂载者
在上）。后端侧该锁已同日补齐：`set_agent_slot` 的框架钉选门禁
（[[slot_service]] 2026-07-18）会 403 与 owner 默认不同的钉选——前端
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
见 [[user_service]] / [[agentFramework]] 2026-07-10)。相应删掉
`isCodexFramework` / `CODEX_ALLOWED_PROVIDER_SOURCES` 两个 import。

## 2026-07-09 — per-agent helper 放开 OAuth + 默认便宜模型

与 [[ModelDefaultsSettings]] 同步(per-agent 覆盖面板):`helperProviders` 去掉
`p.auth_type !== 'oauth'` 过滤,helper 可选 OAuth;选定 provider 后默认 model 用
`defaultHelperModel`(见 [[agentFramework]])挑便宜的 mini/haiku 而非旗舰;提示文案改为
"OAuth 也能用"。后端 `validate_slot_binding` 已一致放开 helper 的 OAuth(见
[[user_service]]),所以 per-agent override 也能绑 OAuth helper。

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
