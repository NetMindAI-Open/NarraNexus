---
code_file: frontend/src/platform/builtin.ts
last_verified: 2026-09-24
stub: false
---

## 2026-09-24 ui.toolRenderers（第 18 个注册表）

`builtin.browser` 注册 `browser_look` 的输出行渲染器，与浏览器面板同 owner，禁用浏览器插件时一起消失。
组件 **lazy**（守住本文件「首块只有元数据」的约定）：2026-09-24 先写成 eager import，结果把
`lib/utils`+真实 i18n 带进 vitest 全局 setup，`timeLocale` 测试的 `vi.mock('@/i18n')` 失效——症状是
日期不随语言变，根因是首块被组件拖大。`accepts` 用无依赖的 `lib/browserLook` 解析器，可以留在首块。

## 2026-09-23 - Browser is owned by its feature plugin

The Browser panel registers with owner `builtin.browser`, matching the existing
feature-level teams pattern. The shell-owned helper would label it `builtin.ui`,
which the plugin loader deliberately never disables. Correct ownership lets
`disableBuiltinUi` remove the panel and blacklist later registrations, so the
registry subscriptions in desktop/mobile navigation and approval notices actually
reflect whether the browser feature is enabled.

## 2026-09-07 — 注册内置 artifact kinds

`BUILTIN_ARTIFACT_KINDS` 的每个 kind 以 owner `builtin.ui` 注册进 `ARTIFACT_KINDS`——壳对自己注册表的贡献仍只在这一处。
`kindRegistry.ts` 只含 lazy 渲染器 thunk，首屏 chunk 不因此变重。

## 2026-09-07（批 1 三轮复审移植）— 共用 lazy 的 BundleImport；面板注册走 `builtinPanel(id: BuiltinTabId, …)`

`bundle/import` 与 `templates/install` 此前各自 `lazy()` 一次成了两个组件类型、导航间 remount；现在共用一个常量。
12 条 `PANELS.register` 改经 `builtinPanel`，id 参数类型是 `components/bookmarks/builtinTabIds.ts` 的 `BuiltinTabId`：
壳自己写的 id 拼错即编译错误，插件 id 仍是开放的 `string`（条带由注册表派生）。

## 2026-09-03 — 壳自己的贡献（页面、侧栏、面板）

「内置即插件」（D4）：壳通过与插件相同的注册表贡献自己的页面/侧栏行/面板，集中在一个文件、按 UI 顺序。
`main.tsx` 在首帧前 import 一次。页面与面板组件保持 lazy，本文件只往首包加元数据。
设置分区**不在这里**注册：它们由 `pages/settings/registerBuiltinSections.ts` 在设置 chunk 内注册，
以保持面板与页面同 chunk、同步渲染（否则 SettingsPage 测试与 bundle 形状都会变）。

## 2026-09-04 · builtin.teams as a feature-level plugin (batch 3c.2)

Feature-level builtins register with their plugin id as owner (`TEAMS = { owner: 'builtin.teams' }` for teams-new/team-detail/team-chat) so `disableBuiltinUi` can remove the whole row; shell pages keep `builtin.ui`.

## 2026-09-04 · UI slot points (batch 3d.2)

Registers the conversation kinds `chat` (shell) and `team` (owner builtin.teams, gone with the plugin) so `when: conversationKind:<k>` has something to name.

## 2026-09-07 — every builtin panel gets `strip` metadata (I-3)

All 12 `PANELS.register(...)` calls (builder, awareness, workspace, channels, smarthome, jobs,
inbox, artifacts, memory, social, skills, mcp) now pass a `strip` object (label/labelKey/icon/
category/order, plus `conditional: 'studio'` for builder and `stripLabel`/`stripLabelKey` for
social/mcp) instead of leaving the bookmarks drawer's strip layout as a separate hardcoded table
in `bookmarks/tabs.ts` that had to be kept in sync by hand. New lucide-react icon imports
(FolderOpen, Home, Inbox, ListTodo, Network, Puzzle, Radio, Sparkles, Wand2) and
`ArtifactsGlyph` from `@/components/bookmarks/tabs` back this. Because this file imports
`ArtifactsGlyph` from `tabs.ts` BEFORE its own `PANELS.register(...)` calls run, `tabs.ts`'s strip
helpers must be functions computed from `PANELS.list()` at call time, not module-level consts
computed once at import — see `tabs.ts`'s mirror doc for the import-order hazard this avoids.
