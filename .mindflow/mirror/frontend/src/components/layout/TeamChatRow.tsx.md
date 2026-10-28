---
code_file: frontend/src/components/layout/TeamChatRow.tsx
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — 不再透传 `onClearData`

随 [[TeamRowMenu.tsx]] 的菜单项一起去掉。


## 2026-08-18 — 行悬停改 --nm-row-hover

与 AgentGroupSection 同步:行悬停 = 选中色减淡档;展开成员子行同改。

## 2026-08-18 — merge ruling: v4 row layout kept, dev's unread dot/preview NOT wired

Owner ruled the v4 trailing structure (count → ⋮ at the row edge) wins over
dev's unread-dot + last-message-preview row. The `unread/preview/authorName`
props from dev were removed here; AgentList still maintains dev's durable
read-watermark (markTeamRead on open), so re-adding the dot later is a
one-prop change. dev's teamUnreadBadge.test.tsx was dropped with the UI —
restore it from dev if the dot comes back.

## 2026-08-11 — ⋮ 钉到行尾,与 agent 行同构

kebab 原在名字后面(位置随名字长短漂移,Owner 截图指出与 agent 行改后不
一致)。现行尾顺序与 AgentGroupSection 完全同构:meta(成员数)在前、⋮
在最右缘;opacity 显隐保留占位。

## 2026-08-06 — 团队行可展开成员(UI/UX 设计文档采纳项)

行首新增 chevron 展开钮(stopPropagation,行本体仍开群聊):展开后缩进
列出成员 agent,点成员跳到该 agent **自己的单聊**(onSelectMember =
AgentList.handleSelectAgent),当前打开的 agent 高亮。members 由
AgentList 从 team.member_agent_ids ⨝ rawAgents 计算传入。
同文档中的拖拽入团 / manage-team 重构方案 Owner 未定,未实现
(见 self_notebook/todo)。

> 2026-06-24：`GroupAvatar` size `md`→`sm` (32px),与 agent 行 + 用户头部统一大小。
> 同时改成**单行**:去掉 "Group chat · N agents" 副标题,成员数 "N agents" 移到右侧
> (像 agent 行的时间戳,`ml-auto`),`items-start`→`items-center`、`py-2`→`py-1.5`,
> 行高与 agent 行一致。
>
> 2026-06-24 (#43)：新增 `onAddAgent(teamId)` + `addingAgent` 两个 prop,原样透传给
> [[TeamRowMenu]] 的 "Add agent" 项。这把"在某 team 下新建 agent"的能力接回了新结构
> (旧入口在已废弃的 [[AgentGroupSection]] team header `+` 上)。Row 自身不持有逻辑,
> 只做透传;真正的 `createAgent({ teamId })` 在 [[AgentList]]。

> 2026-08-14：新增 `unread` / `preview` / `authorName` 三个 prop。行现在有第二行——
> 房间里最后一句话，和说话的人——和它下面的 agent 行一致；右侧多一个未读圆点。
>
> **是圆点不是数字**，这是数据位置决定的：sidebar 从不加载 transcript，而服务端无法
> 数出"这台设备上的未读数"（水位线在 localStorage 里，逐设备）。判定逻辑不在这里，
> Row 只负责渲染——`teamHasUnread` 在 [[unread.ts]]，服务端那一半在 [[teams.py]] 的
> `_team_room_activity`。

# layout/TeamChatRow.tsx — One team's group-chat entry in the sidebar

## Why it exists

The sidebar groups chats into a **TEAMS** section (group chats) over an
**AGENTS** section (every agent once); see [[AgentList]]. This is one row in the
TEAMS section — extracted out of [[AgentGroupSection]] so teams live in their own
top section instead of being interleaved with their member agents (which also
made an agent in two teams appear twice).

## How it works / design

- Row sized like an [[AgentRowMenu]]-bearing agent row: a carbon·silicon split
  `GroupAvatar` (the team is human+AI), the team name, and `Group chat · N
  agents`.
- Owns its OWN inline-rename + `menuOpen` state. The ⋮ menu ([[TeamRowMenu]])
  sits right next to the name (Rename / Delete). `onRename`/`onDelete`/`onOpen`
  are thunks up to [[AgentList]] (teamsStore.updateTeam / deleteTeam / navigate
  to `/app/teams/:id/chat`).
- `active` (the team whose group chat route is open) paints `--nm-row-active`.
- Gotcha: `onOpen` is suppressed while renaming so clicking the input doesn't
  navigate; `menuOpen` lifts the row's z-index so the ⋮ panel paints above
  sibling rows (each row is its own stacking context).

## 2026-07-22 — onClearData passthrough

New `onClearData(teamId)` prop, forwarded to [[TeamRowMenu]]'s Clear-data item.
