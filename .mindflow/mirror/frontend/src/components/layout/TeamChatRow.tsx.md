---
code_file: frontend/src/components/layout/TeamChatRow.tsx
last_verified: 2026-07-22
stub: false
---

> 2026-06-24：`GroupAvatar` size `md`→`sm` (32px),与 agent 行 + 用户头部统一大小。
> 同时改成**单行**:去掉 "Group chat · N agents" 副标题,成员数 "N agents" 移到右侧
> (像 agent 行的时间戳,`ml-auto`),`items-start`→`items-center`、`py-2`→`py-1.5`,
> 行高与 agent 行一致。
>
> 2026-06-24 (#43)：新增 `onAddAgent(teamId)` + `addingAgent` 两个 prop,原样透传给
> [[TeamRowMenu]] 的 "Add agent" 项。这把"在某 team 下新建 agent"的能力接回了新结构
> (旧入口在已废弃的 [[AgentGroupSection]] team header `+` 上)。Row 自身不持有逻辑,
> 只做透传;真正的 `createAgent({ teamId })` 在 [[AgentList]]。

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
