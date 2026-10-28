---
code_file: frontend/src/components/settings/ProviderSummaryCard.tsx
last_verified: 2026-09-11
stub: false
---

## 2026-09-11 — framework state starts empty

The framework state starts as `''` instead of `'claude_code'`: if the
agent-framework request fails the Agent row now reads "— · <provider>" rather
than claiming Claude Code. Pinned by `__tests__/ProviderSummaryCard.framework.test.tsx`.

## 2026-09-07 — the framework label uses the live `display_name` (B6)

The local `FRAMEWORK_LABELS` hardcoded table is gone. The Agent row's detail text now calls
`lib/frameworkBrand.ts`'s `formatFrameworkFromList(framework, frameworks)`, where `frameworks` is
the `frameworks[]` array already returned alongside `framework` by the SAME
`api.getAgentFramework()` call this component already made (previously only `fw.data.framework`
was kept; `fw.data.frameworks` is now also stored in state). This prefers the backend's own
`display_name` per framework entry over the static picker label, with the same fallback chain as
`formatFramework` when the live entry has no `display_name` or the list has not loaded.

## 2026-09-03 — 注释里的 `/setup` 改为「first-run」

`SetupPage` 在本次退役（换成 [[../../pages/WelcomePage]]，`/setup` 路由保留为
指向 `/welcome` 的重定向）。本组件注释里那句「/setup 的逻辑：先给简单表面，
完整 ProviderSettings 藏在后面」改成「first-run 的逻辑」——描述的是那套渐进披露
的意图，不是某一个页面文件。

行为未改。

## 2026-08-28 — ProviderInfo 成为共享 ProviderRow 的别名

`api.getProviders()` 的 providers 值类型是共享 `ProviderRow`;本组件的
本地 `ProviderInfo` 改为它的别名,**双重 cast 已删除**(第 4 轮:窄共享
类型 + 富本地类型的组合逼出过 `as unknown as`,等于关掉这一处的全部类型
检查)。行字段的唯一定义在 [[providersApi]]。

# ProviderSummaryCard.tsx — at-a-glance LLM wiring summary

## 2026-07-29 — 框架标签显示 NexusPower-beta

卡片上的框架名从 [[agentFramework]] 的 `label` 取，于是自动显示
`NexusPower-beta`（beta 后缀是产品决定，写在框架表里，不在这儿硬编码）。

## Why it exists

The Settings → Providers section used to lead with the full 1400-line
ProviderSettings. After the 2026-06-10 one-key onboarding work, Settings
adopts the same logic as first-run /setup: a simple face first, the full
UI behind an "Advanced configuration" disclosure. This card IS that
simple face for users who already have providers: three read-only rows —
Agent (framework · model · provider), Helper (model · provider), Keys
(active providers with masked keys).

## Upstream / downstream

- **Rendered by**: `pages/SettingsPage.tsx` `ProvidersSection` (only
  when providerCount > 0; zero-provider users get OneKeyOnboard
  instead).
- **Reads**: `api.getProviders()` (providers + slots) and
  `api.getAgentFramework()` (framework label), fetched in parallel.
- **refreshToken prop**: parent bumps it when the Advanced disclosure
  closes or onboard completes, so the summary reflects edits made in
  the full UI.

## Design decisions

- **Strictly read-only** — every edit affordance stays in Advanced
  (ProviderSettings). No quick-switchers here; that would re-grow the
  complexity this card exists to hide.
- Backend-unready states render nothing (null) rather than a broken
  card; the section header + disclosure still show.
- NOTE: `api.getAgentFramework` runs an auth probe server-side
  (subprocess, ~100ms-10s worst case) — acceptable for a settings page
  load; if it ever becomes a drag, add a probe-free variant.
