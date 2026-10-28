---
code_file: frontend/src/components/settings/__tests__/ModelDefaultsSettings.test.tsx
last_verified: 2026-09-11
stub: false
---

## 2026-09-11 (r4) — follow-up of PR #399's last-review minors

- thinking / reasoning effort disabled on an unbound slot, enabled once a
  provider is picked; a framework switch that drops the bound card locks them
  again (both red when the `disabled` props are removed);
- every select label's `control` is its select (red without `htmlFor`);
- all selects — framework, agent provider/model/thinking/effort, helper
  provider/model — are located by accessible name; the mock translator maps
  `helperTitle` to "Helper model" for that. No positional or option-content
  combobox lookups remain.

## 2026-09-11 (r3) — third review of PR #399 (minors)

- bound card kept by a nexus_power switch, user picks the blank provider →
  `pickAgentModel`, no framework or slot write, no "Saved";
- (superseded in r4: thinking can no longer be edited on an unbound slot)
- agent provider / model selects located by accessible name
  (`agentProviderSelect()` / `agentModelSelect()`), no positional combobox
  index for agent selects any more.

Both new cases were checked red with the old `frameworkOnlyAgent` condition.

## 2026-09-11 (r2) — second review of PR #399

- framework switch empties the agent draft, Save with no card → only
  `setAgentFramework('codex_cli')`, no slot write, `slotClearedPickModel`, no
  "Saved", no override-stats fetch; a half-filled agent draft (provider, no
  model) with a framework change is still refused with nothing written;
- `getAgentFramework` `success:false` → `loadFailed` shown, Save disabled;
  a successful load shows no load error;
- rollback lands but the re-PUT of the cleared binding fails →
  `frameworkRestoredBindingLost` (not `frameworkSavedSlotFailed`), drafts kept;
- rollback fails → `frameworkSavedSlotFailed` and the agent pick survives the
  reload (Save enabled);
- helper fails after only the framework landed → `frameworkSavedHelperFailed`;
  after the agent slot landed → `agentSavedHelperFailed`.

Each new case was checked red against the previous component.

## 2026-09-11 — unbound slot, rollback and restore cases (review of PR #399)

- unbound agent slot + framework-only change → Save calls only
  `setAgentFramework`, no `pickAgentModel` error;
- codex (drops the anthropic card) → back to claude_code restores `p_own` and
  the form is clean; a provider picked after the drop is not overwritten;
- framework lands, agent slot write fails → framework POSTed back to
  `claude_code`, cleared binding re-PUT (`p_own`), `slotSaveRolledBack`, draft
  still dirty;
- same, but the rollback throws → stored state reloaded (codex_cli, empty
  slot), `frameworkSavedSlotFailed`;
- framework lands, helper write fails → helper edit kept, Save still enabled
  (message: see r2 above).

Each was checked red by reverting the corresponding branch in `apply()` /
`onFrameworkChange`.

## 2026-09-11 — framework-as-draft cases

The old "switching framework drops the binding only when the backend cleared it"
case pinned the immediate-persist design (Save disabled after a framework pick) —
that design IS the Owner bug, so it is replaced by: changing only the framework
enables Save and Save calls `setAgentFramework` (no slot write when the bound
provider still backs it); picking the stored framework back makes the form clean;
a framework the bound provider cannot drive clears the provider in the draft
(saving then is covered by the r2 section), and after picking a compatible
card the framework is written BEFORE the slot (invocation order asserted); a
rejected framework save shows the error and keeps the draft dirty. The cloud /
staff cases now assert the pick lands in the draft (select value, Save enabled)
instead of an immediate API call.

## 2026-08-28 — 插件门禁：disabled 选项 + 弹窗拦截

新增一条用例：`mockGetAgentFramework` 返回 `frameworks` 里
`codex_cli: available=false`，断言该 `<option>` 带 `disabled`
（可见但不可选，不是隐藏）；再用 `fireEvent.change` 模拟程序化选中它，断言
弹出"Plugin required"提示、`<select>` 值弹回、`setAgentFramework`
未被调用——覆盖 disabled 属性挡不住的 change 事件路径。

# ModelDefaultsSettings.test.tsx

钉住 Model Defaults 编辑器的云端 netmind-only 前端行为（api / i18n /
configStore / runtimeConfig 全 mock）：云端普通用户两个 provider 下拉只剩
netmind 卡 + 底部"下载本地版"note + 框架下拉可交互但选到不同框架时弹
useConfirm 样式弹窗、值弹回、`setAgentFramework` 不被调用、点 OK 消失；
云端 staff 全量选项、无弹窗、正常切框架；本地全开无 note。i18n mock 使用
稳定的 `t` 引用，并为断言涉及的 key 提供英文测试文案；稳定引用也保持与
react-i18next 的 effect 依赖语义一致，避免 mock 自身制造重复加载。
