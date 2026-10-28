---
code_file: frontend/src/components/layout/TeamRowMenu.tsx
last_verified: 2026-09-11
stub: false
---

## 2026-09-11 — the dropdown shell moved to RowKebabMenu

Same extraction as [[AgentRowMenu]]: this file is now the three items (Add
agent — disabled while `addingAgent` — / Rename / Delete) on the shared
[[RowKebabMenu]]. The trigger gained `type="button"` and `aria-expanded`,
which the agent menu already had. The item handlers still receive the click
event.

## 2026-09-11 — file header: no longer "the last per-row menu"

Comment-only: [[AgentRowMenu]] is back on agent rows (Owner-required), so the
header now describes this menu by its own items and names its sibling.

## 2026-09-03 — 文件头改写：侧栏最后一个行菜单

注释从「镜像 AgentRowMenu，让团队行有和 agent 行一样的操作」改成陈述它现在的
处境：agent 行的菜单已于 2026-08-27 退役（动作移到 agent 档案页），而**团队没有
档案页**可以承接改名 / 清理 / 删除，所以这个菜单留下来了。

这不是风格问题——它解释了为什么侧栏里只剩一个行菜单，避免后来者以为是漏删。
行为未改。

## 2026-09-03 — 去掉「清理数据」项

`onClearData` prop 与菜单项删除;清理入口统一在房间的团队管理 tab
([[../chat/team/TeamManagePanel.tsx]])。加 agent/改名/删除三项不动。i18n `layout.teamRowMenu.clearData`(zh/en)随之删除。


## 2026-08-19 — 点击页面任意处可关闭 + Add agent 接入 i18n

与 [[AgentRowMenu]] 同改:backdrop 换 [[useDismissOnOutside]](transform
祖先陷阱,详见彼处)。另:「Add agent / Adding…」两处硬编码英文改走
`layout.teamRowMenu.{addAgent,addingAgent}`(10 locale)。

> 2026-06-24: added an **Add agent** item (UserPlus, above Rename). This
> re-homes dev's #43 "create an agent already in this team" capability — the old
> hover-`+` lived on the [[AgentGroupSection]] team header, which no longer
> exists now that teams render as single [[TeamChatRow]]s. `onAddAgent` +
> `addingAgent` (disables the item / shows "Adding…" mid-create) come down from
> [[AgentList]] (`handleCreateAgentInTeam` → `createAgent({ teamId })`).
> `MenuItem` gained a `disabled` prop for this.

# layout/TeamRowMenu.tsx — Kebab (⋮) menu for the team group-chat row

## Why it exists

Mirrors [[AgentRowMenu]] so a team row ([[TeamChatRow]]) offers the same
Add agent / Rename / Delete affordances as an agent row. Inline absolute panel
(no portal) so it works inside the sidebar scroll container.

## Gotcha

`onOpenChange` is fired from the click handler (`setOpenAndNotify`), NOT from
inside a `setState` updater — calling the parent's setter during render
triggers React's "cannot update a component while rendering a different
component" warning. (AgentRowMenu had this latent bug; both are now fixed the
same way.)

## 2026-07-22 — Clear data item

Added an `onClearData` prop + "Clear data" MenuItem (Eraser icon, between Rename and Delete),
mirroring the agent row's clear-data affordance. Opens [[ClearTeamDataDialog]] via
[[TeamChatRow]] → [[AgentList]]. i18n `layout.teamRowMenu.clearData`.
