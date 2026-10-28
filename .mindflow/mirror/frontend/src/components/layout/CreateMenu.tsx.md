---
code_file: frontend/src/components/layout/CreateMenu.tsx
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — 交叉引用改指 TeamRowMenu

文件头注释里「同 AgentRowMenu 的内联面板做法」改指 [[TeamRowMenu]] —— agent 行
菜单在本次一并删除（它的动作搬去了 [[../../pages/AgentProfilePage]]），
[[TeamRowMenu]] 成了侧栏里**唯一**还在的行菜单，也就成了这个做法的参照。

行为一字未改，只是原来的参照对象不存在了。

## 2026-08-19 — 点击页面任意处可关闭

backdrop 换 [[useDismissOnOutside]],与两个行菜单同批(transform 祖先陷阱,
详见 [[TeamRowMenu]])。

## 2026-08-06 — Chat UI v4:从 ⊕ 图标改为全宽 "New" 导航行

触发器从 AGENTS 头部的 icon 按钮变为 Sidebar 全局导航第一行(Plus + "New")。
下拉项扩为四个:Create agent / Create team / Import .nxbundle(新增
onImportBundle,→ /app/bundle/import)/ Import from other source(仍仅
local mode 出现)。仍是 inline absolute 面板(非 Radix portal)。
`importAgent` 文案从 "Create Agent (from other source)" 改为
"Import from other source"(10 语言同步)。

# layout/CreateMenu.tsx — The "+" create dropdown (Agent / Team / Import)

## Why it exists

Surfaces teams as a first-class creatable object alongside agents (the
homepage's team-first model). Replaces the former single create-agent "+"
button in [[AgentList]]'s header with a dropdown: **Create Agent**
(the existing `useCreateAgent` flow), **Create Team** (opens
[[TeamManagementModal]], whose left column is the create-team form), and an
optional **Create Agent (from other source)** — the Agent Migration entry
point ([[ImportAgentModal]]).

## Design

Mirrors [[AgentsHeaderMenu]]'s inline-panel approach (no Radix portal) so it
renders correctly inside the sidebar scroll container. Pure menu — all items
are thunks passed in by AgentList. `onImportAgent` is **optional**: AgentList
only passes it in local/desktop mode, since the migration scanner reads the
filesystem and 503s on cloud. When absent, the import item is hidden.
