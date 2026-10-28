---
code_file: frontend/src/hooks/index.ts
last_verified: 2026-09-11
stub: false
---

## 2026-09-11 — exports `useAgentActions` (+ `AgentActionDialogs`, `DeleteAgentOutcome`)

Shared rename / delete for the profile page and the reinstated sidebar agent
row ⋯ menu ([[../components/layout/AgentRowMenu]] is back as a self-drawn popover
consumer of `useDismissOnOutside`).

## 2026-09-10 — 导出 `useCircuitBannerAutoClear`

熔断横幅自愈轮询钩子（[[useCircuitBannerAutoClear]]），App.tsx 经 barrel 引用。

## 2026-09-08 — 导出 `usePluginTheme`

插件主题应用钩子（见 `usePluginTheme.ts.md`）。

## 2026-09-04 — 再导出 `useStudioLifecycle`

见 [[useStudioLifecycle.ts]]。

## 2026-09-03 — 新增 `useAgentImport` 导出

barrel 新增 `useAgentImport` 与 `AgentImportController` 类型，供批量导入队列
使用（队列本体在 `lib/migrationImportQueue.ts`，控制器把「排队 / 逐个 apply /
按 import_id 催促」这套状态收在一个 hook 里，让 [[../components/layout/ImportAgentModal]]
只消费状态而不自己编排）。

同批清理：08-19 那条里列的五个自绘弹层少了一个 —— `AgentRowMenu` 在本次删除，
它的引用已从该条移除。

## 2026-08-19

新增 `useDismissOnOutside` 导出([[useDismissOnOutside]])——五个自绘弹层
([[../components/layout/TeamRowMenu]] /
[[../components/layout/CreateMenu]] / [[../components/layout/Sidebar]] 账户弹层 /
[[../components/chat/ChatHeader]] ⋯ 菜单)经 barrel 引用。

## 2026-08-14

Added `useFastMode` export ([[useFastMode]]) — [[ChatPanel]] 经 barrel
引用（与 useAgentWebSocket 同路径心智）。

# index.ts — Hooks barrel export

## Why it exists

Provides a single import path `@/hooks` for the four hooks used across multiple components: `useTheme`, `useAgentWebSocket`, `useTimezoneSync`, and `useAutoRefresh`.

## Notes

`useSkills` is intentionally not re-exported here — it is only used inside the Skills panel and is imported directly from `@/hooks/useSkills`. Adding it to the barrel would not be harmful but would suggest it is more widely shared than it is.

## 2026-06-10

Added `useBookmarkSignals` export ([[useBookmarkSignals]]).

## 2026-07-30

Added `useAgentImported` export ([[useAgentImported]]) — the shared post-import
side effect used by [[AgentList]] and [[MigrationGuide]].
