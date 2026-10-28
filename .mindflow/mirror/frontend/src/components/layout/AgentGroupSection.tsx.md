---
code_file: frontend/src/components/layout/AgentGroupSection.tsx
last_verified: 2026-09-11
stub: false
---

## 2026-09-11 — the rename once-guard is a ref, not the `renaming` state

Enter/Escape unmount the inline input; WebKit (the Tauri webviews) dispatches
blur on removal, and that blur handler runs from the same render's closure,
where `renaming` is still true — so a state-based guard let Enter-then-blur
send two renames. `renameSettledRef` is set by the first settle (commit OR
Escape cancel) and reset by `startRename`; Escape sets it too, otherwise the
unmount blur after Escape would commit the discarded draft. Pinned by
`agentGroupSection.test.tsx` (Enter+blur and Escape+blur fired inside one
`act()`, i.e. with no render in between).

## 2026-09-11 — owner rows get the ⋯ menu again + inline rename (OWNER-REQUIRED)

Reverses the "rows are display-only" state from #383: an agent row the viewer
owns (`created_by === currentUserId`) renders [[AgentRowMenu]] at the trailing
edge (hover-revealed like the team row's, stays visible while open), and
someone else's public agent stays display-only. New optional prop
`rowActions: AgentRowActions` (`onRename(agentId, name)`,
`onOpenModelConfig(agentId)`, `onDelete(agentId)`) — omitted → no menu; the
host (AgentList) owns API calls, confirm dialog and the config panel.
Rename swaps the name line for an inline input (Enter / blur commit, Escape
cancels; a blank or unchanged name is not sent; the commit guard makes the
Enter-then-blur pair act once); the row ignores clicks while renaming. An open
menu lifts the row (`relative z-30`) above the next row's stacking context.
Owner-required entry — do not strip it back to display-only.

## 2026-08-27 — 行上的 ⋮ 菜单整个拿掉,行变成纯导航

Owner:侧边栏 chat 行不该带操作入口。`AgentRowMenu` 连同它驱动的
inline 快速改名一起删除——两者都只能从这个 kebab 触发,留着就是没有
入口的死代码。随之消失的还有 2026-06-11 那个 z-lift(`menuOpen &&
relative z-30`):没有浮层要盖住下一行,行不再需要抬栈。

props 从 19 个降到 9 个:`showPublicToggle` / `editing*` / `onSaveEdit` /
`onCancelEdit` / `savingName` / `onStartEdit` / `onEditAgent` /
`onClearData` / `onDelete` / `onTogglePublic` / `deletingAgentId` 全部
删除。`currentUserId` **保留**——它还有第二个用途:判断 `isOwner` 以决定
是否给别人的 public agent 挂那个只读 Globe 徽章。

改名 / 描述去 [[../../pages/AgentProfilePage.tsx]] 的 Settings tab,
清数据 + 删除去同一页头部的 ⋮(见该页 md 同日条目)。

## 2026-08-18 — 行悬停改 --nm-row-hover(选中色的减淡档)

悬停(warm)与选中(灰)不同色系读起来像两种无关高亮;现在行悬停用
row-active 同色系约半强度的新 token。kebab 等控件悬停仍 paper-warm(§2.5)。

## 2026-08-11 — 折叠三角换 lucide;kebab 钉到行尾

Owner 对照截图两处修缮:1) 分组头的 "▶" 字符三角(实心)换成 lucide
ChevronRight + rotate-90,与 TeamChatRow 的展开箭头同语言(design_system.md
§5 禁实心/线性混用);2) AgentRowMenu kebab 从名字后面挪到行尾 meta
(unread/时间)之后——原位置随名字长短漂移。opacity 显隐保留占位,悬停时
时间戳不位移。

## 2026-07-23 — 透传 onEditAgent + inline 改名加 maxLength

Section/Row 两处 props 新增 `onEditAgent`,与 `onStartEdit`/`onClearData` 同样
一路传到 [[AgentRowMenu.tsx]] 的"编辑…"项。inline 快速改名的 `<input>` 加
`maxLength={AGENT_TEXT_MAX_LENGTH}`(255),让快速改名路径也无法超限。

## 2026-07-10 — threads `onClearData`

Additive: a new `onClearData(agent, e)` prop is threaded from [[AgentList.tsx]]
through both the section and the private `AgentRow` down to
[[AgentRowMenu.tsx]]'s new "Clear data…" item (same pattern as `onDelete`).

## 2026-06-24 — compact single-line agent rows (denser list)

Owner: shrink the rows so more agents fit. `AgentRow` is now ONE line —
**chat preview dropped** (it also conflated group-chat content into the 1:1
list, an unfixable historical-data leak), avatar down to `size="sm"` (32px),
row padding `py-2`→`py-1.5`, container `items-start`→`items-center`. The line
is name + public globe + **kebab next to the name** (not flex-1 on the name, so
the ⋮ hugs it like TeamChatRow's), then the unread pill + time pushed to the
right edge via `ml-auto`. `getRowMeta().preview` is no longer read here. Avatar
is `size="sm"` (32px) to match teams + the user header (all sm now).

## 2026-06-23 — slimmed to agent rows only; group chats moved out

The group-chat row + team rename/delete/open logic was extracted to
[[TeamChatRow]] (now rendered in [[AgentList]]'s TEAMS section). This component
is now just the section header (optional) + the agent rows. It keeps
`activeTeamChatId` only to compute `effectiveAgentId` — when a team group chat is
open, NO agent row should look selected. In the new layout AgentList always
passes `hideHeader` + `teamId=null` (a single flat AGENTS list), so the header
path is effectively vestigial but retained for the tests / ungrouped case.

# AgentGroupSection.tsx — One collapsible team section in the grouped sidebar

## 2026-06-11 (v1.8.1) — row z-lift while kebab open

Rows retain a transform from their entrance animation → sibling
stacking contexts → DOM order beat the kebab panel's z-index. The row
adds `relative z-30` while its menu is open (state lifted from
[[AgentRowMenu]] onOpenChange).

## 为什么存在

The 2026-06-10 sidebar redesign replaced the TeamFilterBar chip filter
with grouped sections (spec §11): the team is no longer a hidden filter
state above the list, it IS the list's structure. This component owns
one section: full-width header (disclosure triangle + team color dot +
name + member count) and the agent rows beneath it. Extracted from
AgentList so the list file stays orchestration-only.

## 上下游关系

- **被谁用**: `AgentList` (one instance per group from `buildAgentGroups`).
- **依赖谁**: `AgentRowMenu` (kebab), `agentGroupUtils.aggregateSectionUnread`,
  `RingAvatar` (nm), `AgentInfo` from `@/types`. All mutations (rename /
  delete / toggle-public / select) are callbacks owned by AgentList.

## 设计决策

- **Header is typography, not a chip** (spec design principle #1):
  full-width row, so team-name length never changes shape — this is
  what killed the "ragged chip cloud" complaint.
- Collapsed section shows an aggregated unread pill in the header —
  collapsing must not hide information (iron rule #16 spirit).
- `hideHeader` covers the pure no-teams scenario: a single Ungrouped
  header with nothing to contrast against is noise, so AgentList
  renders one headerless section (rows always visible — a headerless
  section cannot be collapsed).
- Hover-visible `→` on named team headers navigates to team detail —
  replaces the old undiscoverable double-click on chips. Ungrouped has
  no detail page, hence no arrow.
- `isOwner` is derived per-row from `agent.created_by === currentUserId`
  (threaded from AgentList) and gates delete / public-toggle in the
  kebab. The read-only Globe badge for OTHER users' public agents stays
  inline per the SHOW_AGENT_PUBLIC_TOGGLE flag contract (see
  AgentList.tsx.md).
- `AvatarWithStreaming` is exported and reused by AgentList's collapsed
  avatar rail so both renderings keep the identical streaming-halo
  treatment.
- Row visual contract preserved from the pre-redesign AgentList row
  (see AgentList.tsx.md 2026-05-19 entry): bg priority selected
  (--nm-row-active) > unread (--color-silicon-soft) > hover
  (--nm-paper-warm); preview = latest assistant reply; unread pill =
  transparent bg + ink30 hairline.

## 新人易踩的坑

Rename commit fires from both mouse (buttons) and keyboard
(Enter/Escape) — `onSaveEdit`/`onCancelEdit` are typed
`React.SyntheticEvent`, not MouseEvent. Don't narrow them back.

Merged with the plugin platform (2026-09-06): pages, drawer panels, sidebar items, commands and agent-row badges come from the frontend registries (`platform/registries`, registered in `platform/builtin.ts`); this file keeps dev's behaviour on top of that.
