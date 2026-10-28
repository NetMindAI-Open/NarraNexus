---
code_file: frontend/src/components/teams/TeamManagementModal.tsx
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — 资料区改用共享的 TeamProfileForm；房间的管理 tab **不再挂本弹窗**

名称/颜色/简介三个字段与它们的 Save 抽成 [[TeamProfileForm]](本弹窗把「删除团队」按钮放进
它的 `trailing` 槽);组长 select 作为表单 `children` 放在 Save 行上方,**整个对话框只有一个 Save**(`handleSave`):
表单实时草稿 + `editLead` 实时值一次提交四项。曾短暂拆成两个同名「保存更改」各存一半,结果
`refresh()` 带回新 `updated_at` 把另一半没保存的草稿重播掉(auto-review round 3 I1),已合回。
组长读的是实时 state 不是 open 时快照——弹窗打开期间它是唯一编辑者,不存在被覆盖的旁路。
提交值 = 显示值(`effectiveLead`):组长已不是成员(后端 `remove_member` 不清组长也不 bump `updated_at`)时
select 显示「Auto」,保存也提交 `""`(清空回落到最早加入者),否则该团队每次保存都 400
(`lead_agent_id must be a team member`),改名跟着丢(auto-review round 4 I1;dev 上原 `handleSaveMeta` 同病)。
守卫:`__tests__/teamManagementModal.save.test.tsx`(只有一个 Save;改名+改组长一次调用都在)。
入口不变:[[TeamRowMenu]] → [[AgentList]]、Dashboard 建团队。房间的
[[../chat/team/TeamManagePanel]] 曾短暂以 `profileOnly` 打开本弹窗(同日撤回,auto-review I9:
左栏切团队/建团队/弹窗删除不带房间离开都不该出现在那个 tab 里),现在内联 TeamProfileForm。


## 2026-08-19 — 删除后落到相邻团队

`handleDeleteTeam` 的落点从 `find`(列表第一个幸存者)改为相邻项:被删行
的下一行,列表末尾则取上一行。连续清理多个团队时选中不再每次跳回列表顶部。
仍用删除前的列表计算(store 未刷新),空列表回 null。
测试:teamManagementModal.deleteSelection.test.tsx。

## 2026-08-19 — initialTeamId prop + COLOR_PRESETS 出走

- 新可选 prop `initialTeamId`:open 上升沿 `setSelectedTeamId(initialTeamId)`。
  兜底 effect 加 `!initialTeamId` guard——两个 effect 同一 commit 冲刷时兜底
  跑在后面,不加 guard 会把 caller 指定的团队覆盖成 `teams[0]`;用户在 modal
  内手动切换仍不会被拽回(guard 只看 selectedTeamId 为空)。
- 调用点两处齐改:Dashboard Teams 行级 Manage 传行的 team_id;
  [[TeamDetailPage]] 的 Edit team 传 `useParams` 的 teamId(否则团队 #5 的
  详情页编辑的是团队 #1——与 Dashboard 行同一缺陷)。
- guard 的连带:现存调用点全都传 initialTeamId,兜底分支对它们不可达,所以
  `handleDeleteTeam` 不能再写 `null`(会卡在空态)——改为用**删除前**的列表
  选下一个存活团队(store 此刻还没刷新,必须用 deletedId 排除而非刷新后下标)。
- `COLOR_PRESETS` 移到 [[teamColors]] 单一来源([[CreateTeamPage]] 同吃),
  §6.2 DATA 豁免注释随迁;数组顺序是契约(`[0]` 是新团队默认色,抽取时未动)。

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

## 2026-07-21 — team-lead picker (was "default responder")

Added a `<select>` (Auto = earliest member, or pick a current member) that saves `lead_agent_id`
via `updateTeam`. Backs the no-@mention routing in backend [[teams]]. `""` clears back to Auto.
i18n keys `teams.leadLabel|leadAuto|leadHint`.

**2026-08-20**: relabeled "默认负责人 / Default responder" → "组长 / Team lead" and renamed the
keys `defaultResponder*` → `lead*` so the setter matches the room's 组长 badge (the label
mismatch was why users couldn't find how to set the lead). The lead is now also settable
in-room from the roster — see [[TeamRosterPanel.tsx]] `onSetLead`.

## 2026-07-22 — clear team data lives in the sidebar ⋮ menu (not here)

"Clear data" is intentionally NOT in this modal — to mirror agents (whose clear-data is only
in the row ⋮ menu), it lives in the team row's [[TeamRowMenu]] → [[AgentList]] renders
[[ClearTeamDataDialog]]. This modal keeps only rename/color/intro/members/team-lead
+ delete.
