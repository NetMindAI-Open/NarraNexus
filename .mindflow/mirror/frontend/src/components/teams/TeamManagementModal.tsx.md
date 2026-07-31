---
code_file: frontend/src/components/teams/TeamManagementModal.tsx
last_verified: 2026-07-30
stub: false
---

## 2026-07-30 — 原生 alert 换成应用内通知

wry（Tauri webview）**不渲染** `window.alert`，调用直接返回、什么都不发生。所以桌面端
创建/保存/删除团队、增删成员失败时**完全无提示**（这四个 catch 块除了 alert 什么都不做，没有 inline 错误态，用户无法区分「失败了」和「我没点上」）。复用了本文件已有的 `useConfirm()` 实例和 `{dialog}` 挂载点，只多取一个 `alert`。改用 [[ConfirmDialog]] 的 `useNotice()`，与仓库既有的 20+ 处 confirm 先例同一条路。

**chrome 不在调用点重复**：标题 / OK 文案 / danger 由 `useNotice` 提供，调用点只写
message。第一版把这三行在 6 个文件里复制了 9 遍（评审点名），改文案要改 9 处。这同时把
`useConfirm` 默认值 `'Notice'` / `'OK'` 硬编码英文、不走 i18n 的洞补在一处 ——
不必去动那个 20+ 调用方共用的原语。共享 key
`common.{noticeTitle,doneTitle,actionFailedTitle,ok}`，10 语言。

`notifyDone` 与 `notifyPending` 是分开的：`noticeTitle` 的 10 个译法都是「请稍候」语义
（稍等一下 / 少しお待ちください / Одну секунду），拿它当成功提示的标题会让用户以为还在
进行中 —— 所以成功走 `doneTitle`。

用一条**仓库级静态契约测试**钉住（`lib/__tests__/no-native-dialogs.test.ts`）：扫描全部
源文件，禁止任何 `window.alert/confirm/prompt` 调用。这类 bug 前两轮都是靠人读代码发现的
—— 单元测试反而 stub 掉了 `window.confirm` 因而什么都没证明。grep 是唯一能覆盖「还没被
写出来的文件」的断言。

# TeamManagementModal.tsx — Full team CRUD modal (create / manage the teams behind the group chats)

## Why it exists

The management surface behind the sidebar's TEAMS section: where the owner
creates teams, sets name/color/intro_md, and adds/removes member agents. Each
team it manages is the roster behind a group chat over the message bus, so
membership edits here directly change who participates in (and is `@mention`-able
within) that team's chat.

## How it works / design

- **Two-column layout**: left is the team list + a create form (name + color
  preset + Create); right is the selected team's metadata (name / color /
  intro_md) plus a member checklist and a Delete button. State is driven entirely
  by [[teamsStore]] (`createTeam` / `updateTeam` / `deleteTeam` / `addMember` /
  `removeMember`); the agent roster comes from `useConfigStore`.
- **Portals to `<body>`** via `createPortal`. The sidebar `<aside>` uses
  `translate` (mobile-drawer slide) which — even at the desktop value of 0px —
  establishes a containing block for `position:fixed` descendants, which would
  trap this overlay inside the 288px sidebar. Rendering into `<body>` escapes
  that subtree so the backdrop+modal are viewport-relative and centered.
- **All member toggles surface failures.** `handleToggleMember` wraps
  add/remove in try/catch and `window.alert`s any backend rejection. Before this,
  the handler leaned on unhandled-rejection propagation, so a 403 (cross-user
  agent / ownership mismatch) or 500 (schema drift / FK violation) silently did
  nothing — the user saw "click Add, nothing happens". Same alert-on-throw
  pattern guards create / save-meta / delete.
- **Gotchas**: `intro_md` edits land directly in `teams.intro_md` and are reused
  as the bundle's default README on export. Imported teams (`source === 'bundle'`)
  get an "imported" badge. Deleting a team only unlinks members — the agents
  themselves are not deleted (the confirm copy says so).

## 2026-07-21 — default-responder picker

Added a "Default responder" `<select>` (Auto = earliest member, or pick a current member) that
saves `lead_agent_id` via `updateTeam`. Backs the no-@mention routing in backend [[teams]].
`""` clears back to Auto. New i18n keys `teams.defaultResponderLabel|Auto|Hint`.

## 2026-07-22 — clear team data lives in the sidebar ⋮ menu (not here)

"Clear data" is intentionally NOT in this modal — to mirror agents (whose clear-data is only
in the row ⋮ menu), it lives in the team row's [[TeamRowMenu]] → [[AgentList]] renders
[[ClearTeamDataDialog]]. This modal keeps only rename/color/intro/members/default-responder
+ delete.
