---
code_file: frontend/src/lib/frameworkBrand.ts
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — `formatFrameworkFromList` prefers the live backend `display_name` (B6)

New export `formatFrameworkFromList(framework, frameworks)`. `GET/POST /api/providers/agent-framework`'s
`frameworks[]` entries now optionally carry their own `display_name`; this function prefers that
over the static `AGENT_FRAMEWORKS.label` this module already centralized, falling back to
`formatFramework` (unchanged) when the list is absent (not loaded yet) or has no entry for the
id. This is the module's SECOND drift-prevention fix in its short life: `ProviderSummaryCard.tsx`
had grown its own `FRAMEWORK_LABELS` hardcoded table and `TeamMemberAvatars.tsx` its own
`formatFramework` — the latter disagreeing with this module's canonical labels on two of three
names ("Codex" instead of "Codex CLI", "Nexus Power" instead of "NexusPower-beta"), exactly the
class of bug this file's own docstring already warns about. Both now call
`formatFrameworkFromList` instead (`TeamMemberAvatars.tsx` passes `undefined` for `frameworks`
since it has no live list of its own, landing on the same static-then-title-case fallback chain
it always used — only the SOURCE of that fallback changed, not its values, except where they
were wrong).

## 2026-09-04 (评审三轮) — 标签不再自己列，转发自 `AGENT_FRAMEWORKS`

首版消掉两份拷贝时又造出第三份标签表，且与既有真源 `agentFramework.AGENT_FRAMEWORKS`
（选择器渲染的那份）两项对不上（"Codex" vs "Codex CLI"、"Nexus Power" vs
"NexusPower-beta"），同一页上能看到两个名字。现在 `formatFramework` 查
`AGENT_FRAMEWORKS`（裸列表，不是按用户 provider 过滤的 `availableFrameworks()`），本模块只
剩图标映射 + 两条兜底。暗色反色判定转发 [[modelBrandIcons.ts]] 的 `iconInvertsInDark`。
测试 `frameworkBrand.test.ts` 钉住「标签等于选择器」「未知 title-case」「缺失 —」。

# frameworkBrand.ts — framework id → 品牌图标 + 渲染兜底（标签转发自 agentFramework）

## 为什么存在

Dashboard 目录表与 Agent 档案页各自拷了一份 `formatFramework` + 图标映射，PR #383 内就漂了
一次（一边认 `nexus_power` 一边不认，tsc 不响）。评审二轮 I2 指出这正是同 PR 两个 🔴 修的
「同一事实两份实现」形状。与 [[modelBrandIcons.ts]]、DashboardPage 的 `CHANNEL_BRANDS`
同一层。

## 两条刻意的行为

- **未知 id** 从原串 title-case，不映射到别的品牌——「不认识但存在」好于「错」。
- **缺失 id**（undefined）渲染 `—` + 通用 Bot 图标。后端对不属于观看者的 agent 留空；
  这里若补默认值就会显示一个它可能没在跑的品牌。

后端 `model_identity.FRAMEWORK_DISPLAY_NAMES` 是 agent 在 prompt 里的自称，属于 prompt
文案，不要求与这里一致。

## 消费方

[[../pages/DashboardPage.tsx]]、[[../pages/AgentProfilePage.tsx]]。
