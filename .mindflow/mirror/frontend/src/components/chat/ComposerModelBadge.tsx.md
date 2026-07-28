---
code_file: frontend/src/components/chat/ComposerModelBadge.tsx
last_verified: 2026-07-28
stub: false
---

## 2026-07-28 — free-tier 锁整个拿掉

那个「免费期间渲染只读 chip」的提前返回删除了，连同 `freeTierModel` state 和
后端 `free_tier` 块的读取。

原因是它守护的前提没了：免费额度现在是一张普通 provider 卡，用户的模型选择
在它上面照常生效。继续锁着，才是那个「切不动模型」的 bug。

## 2026-07-23 — 免费额度锁定态（诚实只读 chip）

新增最高优先级的锁定分支：当 `getAgentLlmConfig` 返回的 `data.free_tier.active`
为真时（[[api]]），渲染**不可点击**的只读 chip `[免费] <model>`（tag + 系统模型名），
tooltip 用 `chat.model.freeTierLocked` 解释"额度用尽后可切换/在面板预设"。

为什么：云端免费额度优先策略在额度有余量时把 per-agent override 整个抢占、锁死系统
固定模型（见 [[resolver]] SYSTEM_OK 分支 + [[agents_llm_config]] 的
`free_tier` 块）。此前徽章照常显示可切下拉、写库、乐观更新，用户以为切成功了实则运行时
永远没变——这正是测试同学报的"model 选择器切换不生效"的真根因。锁定分支优先于
"set model" 和可切换态，因为它决定真正运行的模型。`free_tier.model` 是锁定时实际运行
的系统 agent 模型（非用户自有 slot 模型）。额度耗尽 / 本地模式 → `active=False` → 分支
不触发，切换行为与改前完全一致。Owner 决策：UI 诚实化、不改运行时策略本身。

**该早退是"模型永远切不了"这个 bug 的镜像**（后续重构误伤即复现），故有前端测试守着：
`__tests__/ComposerModelBadge.test.tsx` 两例——锁定态渲染只读 tag + 系统模型、无任何
button/下拉；非锁定态保留原可切按钮 + 展示用户自有模型（review 遗留项，本轮补上）。

## 2026-07-09 — now PER-AGENT (was user-scoped)

The model chip is now the active AGENT's effective model, not the user-level
agent slot. Picking a model writes a per-agent override (PUT
/api/agents/{id}/llm-config/agent via [[api]]) that only affects THIS agent; a
dot marks "custom for this agent" vs inheriting the owner default. Takes
``agentId`` + ``reloadKey`` props ([[ChatPanel]] passes both) — ``reloadKey``
bumps when the header panel saves so the chip re-reads the model.

This chip is a PURE quick model switch: the detailed
[[AgentLlmConfigPanel]] (framework + reasoning + helper) is NOT hosted here — it
lives in [[ChatPanel]], opened from a header ⚙ button left of the cost chip. (An
earlier design put the panel + an entry link inside this dropdown / a standalone
icon next to the chip; both were unclear, so the entry moved to the header icon
cluster.) Falls back to the Settings link only when the owner has no agent slot
at all. Option-building is shared via [[agentFramework]].

# chat/ComposerModelBadge.tsx — in-composer model indicator + one-click switcher

## Why it exists

The conversation model is the user's `agent` provider slot, normally edited in
Settings › Providers. Switching it mid-chat shouldn't require leaving the
composer, so this badge surfaces the current model right in the tools row and
lets the user pick another one from the same provider in a single click. It is
the chat-side convenience face of the existing slot config — it does not invent
a parallel notion of "model"; picking here is exactly the change Settings would
make. This respects binding rule #15: the platform never *chooses* a model for
the user, it only makes the user's own choice quick to reach.

## How it works / design

- Loads the `agent` slot config + that provider's available model list once via
  `api.getProviders()`; choosing a model PUTs the slot through
  `api.setProviderSlot` (optimistic update, reverts on failure) — the same
  endpoint Settings drives, so there is one source of truth, not two.
- Upstream: rendered by [[ChatPanel]] in the composer tools row. Downstream:
  the providers API (`api.getProviders` / `api.setProviderSlot`) and
  `react-router` navigation into `/app/settings`.
- When no slot is configured it degrades to a "set model" link into Settings
  rather than showing a broken/empty switcher; while loading it shows `…`.
- `prettify` trims the `provider/` prefix off model ids for display only — the
  full id is always what's persisted. Gotcha: the dropdown reads from the
  provider's `models` array, so a model the user typed manually elsewhere but
  that isn't in that list won't appear as a pickable option (only the "More in
  settings →" escape hatch covers it).
