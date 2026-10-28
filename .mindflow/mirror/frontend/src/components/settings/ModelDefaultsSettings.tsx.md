---
code_file: frontend/src/components/settings/ModelDefaultsSettings.tsx
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

## 2026-09-11 (r4) — follow-up of PR #399's last-review minors

- **Thinking / reasoning effort are disabled until a provider is chosen**
  (`disabled={!agentDraft.provider_id}`, same rule as the model select). They
  live on the slot row, so on an unbound slot — or after a framework switch
  dropped the card — an edit had nowhere to land (refused with an unrelated
  provider/model message, or silently skipped). `agentDraftEmpty` therefore
  only means "the slot has nothing writable".
- **Every select has an accessible name and a bound label.** Framework,
  thinking and reasoning effort join the agent provider / model on
  `aria-labelledby` (block title + own label); the helper block gets its own
  `useId` (`helperIds`) with the same pattern. Each label also has `htmlFor`
  pointing at its select's `id`, so clicking the label text focuses the select.

## 2026-09-11 (r3) — third review of PR #399 (minors)

- **A user-emptied agent draft is refused, not skipped.** `frameworkOnlyAgent`
  is now `frameworkChanged && agentDraftEmpty && (!agentChanged ||
  droppedByFrameworkRef.current !== null)`: an empty draft counts as
  framework-only only when nobody edited the agent slot, or when the
  framework switch dropped the card (the ref is set). Picking the blank
  provider option by hand together with a framework change now gets
  `pickAgentModel`
  with nothing written — before, the framework landed, the edit was silently
  skipped and the page flashed "Saved".
- **`frameworkRestoredBindingLost` copy** now says the STORED framework was
  put back while the user's new framework choice is still in the form, not
  yet saved (the select keeps showing it so Save can retry).
- The agent provider / model selects are named by `aria-labelledby` (block
  title + their own label, ids from `useId`), so the two "Provider" / "Model"
  pairs on the page have distinct accessible names.

## 2026-09-11 (r2) — second review of PR #399

- **Framework switch that empties the agent draft is saved, then asks for a
  card.** When a framework change drops the bound card from the draft (the new
  framework cannot drive it) and the user saves without picking another one,
  the up-front check no longer refuses: an empty agent draft together with a
  framework change counts as a framework-only save (`frameworkOnlyAgent`) —
  but only when the agent draft is untouched or the framework switch itself
  emptied it (see r3). `apply()` writes the framework, skips the agent-slot
  write, reloads, and — when the backend reported `slot_cleared` — shows
  `slotClearedPickModel` instead of "Saved" and does not offer the
  apply-to-agents dialog (the slot is empty; `dirtySlots` leaves out `agent`
  whenever the agent draft is empty). A half-filled agent draft (provider
  without model) is still refused before anything is written
  (`pickAgentModel`).
- **`load()` surfaces a soft failure of `GET /agent-framework`** (`success:
  false`) as `loadFailed`, same as AgentLlmConfigPanel: with no framework list
  `providerBacksFramework` fails closed and both the framework and agent
  provider selects would render empty with no explanation. No frontend
  framework id is invented; `frameworkInitial` stays `''` so Save stays
  disabled. Later `setError` calls from partial-save paths overwrite it.
- **Partial-save messages name what actually landed.** `rollbackFramework`
  returns `'rolled-back' | 'binding-lost' | 'failed'`: `binding-lost` (the
  framework is back but the cleared binding could not be re-PUT) shows
  `frameworkRestoredBindingLost`; `failed` (framework still switched) shows
  `frameworkSavedSlotFailed`. The probe follows the framework as soon as the
  rollback POST lands. A helper failure after only the framework landed shows
  `frameworkSavedHelperFailed`; after the agent slot landed,
  `agentSavedHelperFailed`.
- **Post-failure reloads keep the user's drafts.** `load(keep?: {framework?,
  agent?, helper?})` re-reads the stored state as the baseline (`*Initial`)
  but keeps the given drafts; after `binding-lost` / `failed` all three are
  kept so Save stays live for a retry, and `droppedByFrameworkRef` is kept
  whenever an agent draft is kept (switching back still restores the dropped
  card). Helper-failure reloads keep only the helper draft.

## 2026-09-11 — framework + agent slot save as one unit (review of PR #399)

- **Framework-only change is savable with an unbound agent slot.** The
  provider+model check runs only when the agent slot itself changed
  (`agentChanged`) — superseded in part by the r2 section above (an empty
  draft left by a framework switch is also saved).
- **No half-commit.** Order is unchanged (framework, agent slot, helper). If
  the agent-slot write fails (`success:false` or throw) after the framework
  landed, `rollbackFramework` POSTs `frameworkInitial` back and, when the
  switch had cleared the binding (`slot_cleared`), re-PUTs `agentInitial`;
  the draft stays as the user left it and the error says nothing was saved
  (`slotSaveRolledBack`). The other outcomes are described in the r2 section.
- **Switching back restores.** A provider/model the framework switch dropped is
  kept in `droppedByFrameworkRef` and put back when the user picks a framework
  that can drive it (checked with `providerBacksFramework`, never blind) and
  has not picked another provider in between; `load()` clears it unless it
  keeps an agent draft.

## 2026-09-11 — the framework is a draft; Save commits it (Owner bug)

Owner report: changing the default framework could not be saved. Root cause:
the framework `<select>` wrote `POST /agent-framework` immediately on change and
the framework was never part of `isDirty`, so after a pick the Save button stayed
disabled (nothing "changed"), no "✓ Saved" ever appeared, and the page gave no
sign the choice had landed.

Now the framework is a draft like every other field: `framework` vs
`frameworkInitial` (both filled by `load()`, initial state `''` — no hardcoded
id), `frameworkChanged` joins `isDirty`, and picking the stored value again makes
the form clean. Picking a framework the bound agent provider cannot drive
(`providerBacksFramework`, the dropdown's own predicate) drops provider/model from
the DRAFT so the user re-picks; a provider both frameworks can drive keeps the
pick. `apply()` requires a provider+model when the agent slot changed (a
framework-only change no longer needs one — see the sections above), then writes the framework FIRST (set_slot validates the provider against
the stored framework), then the agent slot when it changed or the backend
reported `slot_cleared`, then the helper. A framework change counts as an agent
slot change for the apply-to-agents dialog. The auth probe line is hidden while
the draft framework differs from the stored one (the probe describes the stored
framework); the select is disabled while saving.

## 2026-09-10 — "✓ Saved" flag via the shared `useFlashFlag` hook

The 2.5s auto-clear of the Saved indicator now comes from `hooks/useFlashFlag`,
the same hook AgentLlmConfigPanel uses, so the two model editors behave the same:
a second save within 2.5s restarts the countdown instead of the earlier timer
clearing the newer confirmation, and the pending timer is cleared on unmount.

## 2026-09-07 — new `liveFrameworks` state feeds `providerBacksFramework`/`availableFrameworks` (B6)

`lib/agentFramework.ts`'s `providerBacksFramework`/`availableFrameworks` no longer have any
hardcoded fallback for protocol/oauth matching — they read exclusively off the live
`frameworks[]` array from `GET /api/providers/agent-framework` and fail closed (return
false / only-`current`) with no list. This component already fetched that array (for
`frameworkAvailability`, the plugin-install gate) but discarded the array itself; it now also
keeps it in a new `liveFrameworks` state (typed `LiveFrameworkEntry[] | undefined` — `undefined`,
not `[]`, until `load()`'s response lands, so "not loaded" stays distinct from "loaded but
empty") and forwards it as the third argument at both call sites (`agentProviders`'s filter,
`frameworkOptions`'s `availableFrameworks` call).

## 2026-08-28 — 框架下拉套插件可用性 + 新增 `onManagePlugins`

`load()` 多存一份 `frameworkAvailability`（[[agentFramework]] 的
`frameworkAvailabilityMap(fwRes.data.frameworks)`），`frameworkOptions` 在既有
`availableFrameworks()`（钱包过滤，会隐藏）外面再套一层
`withFrameworkAvailability()`（插件门禁，只禁用不隐藏）。`<option>` 对不可用
项加 `disabled` + 文案后缀；onChange 在云端门禁**之前**先查插件门禁——选中未
装插件的框架时弹同款 `showNotice` 样式提示，`e.target.value` 手动拨回，
**不**调用 `setAgentFramework`（这是 disabled 属性挡不住程序化 change 事件的
兜底）。新增可选 prop `onManagePlugins`，与既有 `onManageProviders` 同型，从
[[SettingsPage]] 传入 `() => setActive('plugins')`，提示消息里的链接跳转过去。

## 2026-08-26 — 保存默认后可一键应用到全体 agent

`apply()` 保存 owner 默认成功后，多拉一次 `api.getSlotOverrideStats()`；若旗下
有 agent 存在覆盖（agent 或 helper_llm 计数 > 0），把 stats 塞进 `applyStats`
state 弹出 [[ApplyDefaultsToAgentsDialog]]。零覆盖则什么都不弹，保存路径与旧
行为一致（只写 user_slots）。确认后调 `api.applySlotsToAgents(slots)` 做
clear-to-inherit。这是「改默认 → 覆盖手动调整」需求的入口，改默认本身仍只写
owner 默认，是否应用到 agent 完全由用户在弹框里选。

stats 预览拉取单独包 try/catch：保存已成功后，flaky 的 override-stats GET
（`request()` 非 2xx 会 throw）不得把 UI 翻成「保存失败」，失败就静默不弹框。
`onApply` 里 `applySlotsToAgents` 也自带 catch，避免异常穿透到无 catch 的
Dialog.apply() 造成未处理 rejection。

**2026-08-27 auto-review 修正**：在 `load()` 重置快照**前**捕获本次改动的槽
（`dirtySlots`），传给弹框；仅当**改过的槽**有覆盖才弹（helper-only 改动不会
拿主槽的数字吓人）。`onApply` 成功后用 `showNotice` 回显结果，不再静默关闭（不可逆操作要有结果
反馈）。

**2026-08-27 (r2)**：通知标题用独立 key `applyDoneTitle`（不再复用确认框的
`applyTitle`，避免一 key 两语义）；回显文案 `applyDone` 措辞不再承诺与预览数字
严格相等（预览按有效覆盖数、实际清除含 stub 行，将来可能不同）。`dirtySlots`
state 收成 `Array<'agent'|'helper_llm'>`。

## 2026-07-31 — 框架下拉按钱包过滤 + 切框架不再无条件清 provider

框架下拉从 `AGENT_FRAMEWORKS` 换成
[[agentFramework]] 的 `availableFrameworks(bindableProviders, framework)`，
provider 下拉的过滤换成 `providerBacksFramework`。两者都吃
`bindableProviders`（云端 netmind-only 过滤之后的那份）——用整份钱包算会把
云端根本绑不了的卡算进"能驱动"。隐藏发生时补一行
`frameworkFilteredNote` 说明。

`onFrameworkChange` **不再乐观清空 provider 草稿**：清不清由后端答
（`slot_cleared`），因为两个框架都能驱动的绑定不该被切换白白清掉。为 true
时草稿与 `agentInitial` 一起清——只清草稿会让表单立刻变 dirty，用户看到一个
自己没动过的空 provider。

## 2026-07-29 — 框架门禁改问谓词，NexusPower 云端放行

选择器的拦截条件从 `netmindOnly && e.target.value !== 'claude_code'` 改成
`!frameworkAllowedInCloud(e.target.value, role)`。**下面 2026-07-18 那段
「只在切到非 claude_code 时提示」的判据已被本次改动取代**——那个形状把
NexusPower 一起拒了。

真正被拦的是**会骑用共享 CLI 凭据**的框架（云端镜像单 HOME，`~/.claude` /
`~/.codex` 容器全局、由 staff 登录种下）。NexusPower 直接驱动 provider API、
用所绑卡的 key 且拒绝 OAuth，所以云端放行；`codex_cli` 仍拦。规则只住
[[agentFramework]] 的谓词里，不在这里重新推导——它此前在三处各推导一遍，
NexusPower 上线后三处都还在拒。

弹窗文案随之从「Desktop version only」改为「Staff only in cloud」（10 个语言
包同步）：云端可用的框架不止一个了，旧文案不再是事实。

## 2026-07-29 — 同步走 `frameworkAcceptsProtocol()`

与 [[AgentLlmConfigPanel]] 同一处改动：槽位 provider 过滤从「框架的那一个协议」
变成「框架接受的协议集合」。两个槽位编辑器必须同口径，否则同一个 provider 在
两处一个可选一个不可选。

## 2026-07-23 — 免费额度生效诚实 banner

`load()` 的 `Promise.all` 增拉 `api.getMyQuota()`（[[api]] / [[quota]]），读其
`free_tier`；**该路带 `.catch(() => ({enabled:false}))` 兜底**——banner 只是装饰性告知，
不能让一个 quota/me 的 5xx 把整个面板（provider/model 下拉都靠统一 catch → setError）拖
垮（review 抓出的问题），失败即静默退化为 inactive，与本地模式同一降级路径。
`active` 为真时面板顶部渲染诚实 banner（复用 `chat.model.freeTierBanner`
i18n 键，插值系统模型名）：说明免费额度生效中、当前实际用系统模型、此处默认设置将在额度
用尽后生效。**控件保持可编辑**（允许预配置，与 [[AgentLlmConfigPanel]] 同策略）。这是把
底部 [[ComposerModelBadge]] 只读锁 + Agent 面板 banner 的诚实化补齐到全局层——三个模型
编辑入口在免费额度期都不再静默（运行时都被 [[resolver]] SYSTEM_OK 抢占，区别只
在 UI 有没有告知）。Owner 决策：三入口都 banner、不硬锁；底部快捷徽章例外，保持硬锁。

## 2026-07-18 — 框架选择器弹窗方向化(修云端老 codex 死锁)

弹窗条件原为 `netmindOnly && e.target.value !== framework`——对**任何**切换都弹窗+回退,
包括切回 claude_code,前端也把老 codex 用户锁死。改为 `e.target.value !== 'claude_code'`:
切回 claude_code 放行,只在切到非 claude_code 时提示。与后端 providers.py 403 方向化一致
([[AgentLlmConfigPanel]] 同款)。

## 2026-07-21 — complete model-defaults localization

All user-visible copy in the global default editor now resolves through the
same i18n namespace as the per-agent editor: empty/loading states, explanatory
copy, framework status, provider/model selectors, reasoning controls, helper
recommendation, validation errors, and save feedback. Stored option values
remain unchanged; only their presentation follows the active locale.

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
openai-protocol provider(铁律 #15,见 [[user_service]] /
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
