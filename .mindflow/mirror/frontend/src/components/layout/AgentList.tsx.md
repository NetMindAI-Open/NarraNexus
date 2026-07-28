---
code_file: frontend/src/components/layout/AgentList.tsx
last_verified: 2026-07-23
stub: false
---

## 2026-07-23 — 承载 EditAgentDialog(编辑名称 + 描述)

新增 `editTarget` / `editBusy` 状态 + `handleEditAgent`(打开)/ `doEditAgent`
(调 `api.updateAgent(id, name, description)` 并回写本地 name/description)。行 ⋮
菜单新增"编辑…"入口经 `onEditAgent` 一路透传到 [[AgentRowMenu.tsx]]。这是
`agent_description` 唯一的可编辑 UI(此前该字段进 LLM 上下文/Agent Card 却无编辑入口)。
inline 重命名保留作快速改名。

## 2026-07-17 — flat AGENTS list sorted by recent conversation

The AGENTS section no longer renders `rawAgents` in server/creation
order. A `useMemo`d `sortedAgents` runs [[agentGroupUtils]]
`sortAgentsByActivity(rawAgents, aid => latestMessageMs(session))`,
so the most-recently-active conversation floats to the top and a
chatted agent auto-pins. The local-time source is
`latestMessageMs(agentSessions[aid]?.messages)` from [[unread]] —
counting BOTH user and agent messages, so the agent jumps to the top
the instant the user sends, before the next `/api/auth/agents`
refresh. The currently-open agent participates in the reorder (no
pinning). The backend `/api/auth/agents` now applies the same rule
(minus local sessions) as the pre-hydration baseline, so first paint
is already ordered. BOTH the expanded AGENTS section and the
collapsed avatar rail render `sortedAgents`, so the order doesn't
flip back to creation order when the sidebar is collapsed; TEAMS
rows keep teamsStore order.

Streaming-hot-path guard: the memo does NOT depend on `agentSessions`
directly (a streaming delta rebuilds that object every token). It
depends on `activitySignature` — a cheap O(n) string of each agent's
`id:messageCount:lastMessageTs`. Streaming deltas mutate
`currentEvents`/`currentAssistantMessage`, never `messages` (see
chatStore.updateSession), so the signature is byte-identical across
the per-token churn and the O(n·m) sort re-runs only when a message
is actually committed. `agentSessions` is deliberately omitted from
the dep array (eslint-disable): the closure reads the current
render's value, which is fresh whenever the signature changed. This
honors 铁律 #14 (long sessions are first-class) and #16 (the platform
must not become the interruption source).

Guarded by `__tests__/agentListSorting.test.tsx` — an integration
test that renders the real AgentList against live Zustand stores and
asserts (a) first-paint order follows `last_assistant_at` and (b) a
fresh local `agentSessions` message re-pins its agent to the top.
Deliberately separate from the pure-function unit tests: it fails the
day someone passes `rawAgents` back to `AgentGroupSection` instead of
`sortedAgents`, or drops `agentSessions` from the memo deps — the
exact 2026-07-17 regression where the list stayed in creation order.

## 2026-07-10 — hosts the clear-data wipe dialog

AgentList now owns the "clear conversations / memory" flow: `handleClearData`
sets `clearTarget`; the dialog is mounted conditionally
(`{clearTarget && <ClearAgentDataDialog/>}` — fresh state each open, no reset
effect); `doClearData` calls `api.clearHistory(id, {conversations, memory})`,
then `clearAgent(id)` to refresh the chat view if conversations were cleared,
and surfaces `disk_errors` via `alert`. Wired to rows through
[[AgentGroupSection.tsx]] → [[AgentRowMenu.tsx]] `onClearData`.

> 2026-06-24 (#43): restored `handleCreateAgentInTeam(teamId)` —
> `createAgent({ teamId })` then navigates to that team's group chat so the new
> membership is visible. Wired into each [[TeamChatRow]] via `onAddAgent` +
> `addingAgent`, surfaced in the [[TeamRowMenu]] "Add agent" item. This capability
> was dev's #43; it briefly vanished when the TEAMS/AGENTS restructure replaced
> the old [[AgentGroupSection]] header `+`, and is now re-homed onto the team row.

## 2026-06-23 (PM) — collapsed rail rebuilt to match the expanded list

The collapsed avatar rail no longer groups agents by team (which duplicated an
agent in two teams and showed no group avatar). It now mirrors the expanded
list: team **group avatars** (`GroupAvatar`, carbon·silicon split → open the
group chat) on top, then a **flat deduped** `rawAgents` rail. The "+" is a
portal `Popover` (Create Agent / Create Team) — a plain inline dropdown would
be clipped by the rail's scroll/overflow; its trigger and the manage-agents
button are now 32px circles matching the avatars. `buildAgentGroups` no longer
used here.

## 2026-06-23 — two-section sidebar: TEAMS (group chats) over AGENTS (flat)

The expanded list no longer renders one [[AgentGroupSection]] per team with the
agents interleaved. Instead two collapsible categories (local `CategoryHeader`,
persisted to localStorage): a **TEAMS** section listing one [[TeamChatRow]] per
team (the group chats), then an **AGENTS** section that renders ALL agents once,
flat, via a single headerless `AgentGroupSection` (`teamId=null`,
`agents=rawAgents`). This fixes an agent that belonged to two teams appearing
twice. The toolbar's `+` is now a [[CreateMenu]] (Create Agent / Create Team);
the bracket label reads `CHATS` with the combined teams+agents count.
`activeTeamChatId` (parsed from `/app/teams/:id/chat`) drives the active row +
suppresses agent-row selection while a team chat is open. Per-team collapse
state / `buildAgentGroups`-keyed section collapse was retired (groups are still
used by the collapsed avatar rail).

## 2026-06-10 — Grouped sidebar: teams become sections, chip filter retired

Sidebar redesign (spec §11, branch feat/ui-revamp-user-mgmt). Teams are
no longer a filter ABOVE the list (TeamFilterBar, deleted) — they are
the list's structure. AgentList now derives groups via
[[agentGroupUtils]] and renders one [[AgentGroupSection]] per team plus
an Ungrouped tail; pure no-teams scenario renders a single headerless
section (`hideHeader`). The `filterAgentIds` prop died with the filter.

- Row actions (rename / delete / public-toggle) moved into a kebab
  ([[AgentRowMenu]]); row 1 is avatar + name + time only.
- Import / Export / Manage-teams moved into [[AgentsHeaderMenu]] (⋯) —
  the Users2 dual-entry button is gone, ⋯ is the single team-management
  entry point now.
- Collapsed 72px rail: ALL agents as RingAvatars (old rail silently
  capped at 4) with unread badges, hairline between team groups,
  ∗/∅ glyphs retired.
- Per-team collapse persists to localStorage
  (`sidebar_team_collapsed_v1`); collapsed headers aggregate unread.
- Behavior contracts preserved verbatim: durable unread markers
  ([[unread]]), preview priority (latest assistant, session vs server
  timestamp), active_run-driven spinner, row-bg priority, select →
  navigate back to /app/chat, sticky AGENTS header, useCreateAgent.

## 2026-05-27 — last-activity stamp now includes date context

`getRowMeta` switched from `formatTime` (HH:MM:SS) to
`formatChatTimestamp` so the row stamp differentiates today / yesterday
/ within-week / older. Pre-fix every row showed only `HH:MM:SS` and
the user couldn't tell whether a conversation was fresh or stale. See
[[utils]] for the rendering table.

## 2026-05-21 — unread count uses its own durable read marker

The per-row unread count (`getRowMeta`) used to compare message timestamps
against `lastSeenAwarenessTime:<aid>` — a localStorage marker written ONLY
when the user opened the Awareness tab, never when they read the chat. So
the count zeroed only via the render-time `aid !== agentId` special case and
snapped back the instant the user switched to another agent (the read state
was never persisted). Now it uses [[unread]] (`lib/unread.ts`):
`countUnread(messages, getLastReadMs(aid))`, and a `useEffect` keyed on the
active `agentId` + `agentSessions` calls `markAgentRead` to advance a
dedicated, monotonic `lastReadMessageTime:<aid>` marker. Reading an agent
now durably clears its count. The `completedAgentIds` glowing-dot path is
unchanged (it already cleared correctly on `setActiveAgent`).

## 2026-05-21 — agent creation extracted to useCreateAgent

`handleCreateAgent` no longer holds the create logic — it delegates to the
shared `useCreateAgent` hook (`creatingAgent` state now comes from the
hook too). Reason: the onboarding checklist also creates agents, and both
call sites must share one path (store wiring + the
`first_agent_created` onboarding side effect). See
`.mindflow/mirror/frontend/src/hooks/useCreateAgent.ts.md`.

## 2026-05-19 — NM messenger fidelity pass

Three behavioral changes that together make the sidebar match the NM canonical messenger list, and one bug fix:

- **Sticky `[ AGENTS ]` header**: the `BracketSectionLabel` row plus its action icons (+, Manage, Refresh) are wrapped in a `sticky top-0 z-10 bg-[--nm-paper]` div so they pin to the top of the scroll viewport while the agent rows scroll underneath.
- **Row bg priority rewrite**: selected → `--nm-row-active` (theme-neutral ink overlay); unread but not selected → `--color-silicon-soft` (NM canonical unread bg, reserved for future multi-user fan-out); else transparent. Hover applies `--nm-paper-warm` only when neither selected nor unread. Replaces the earlier opaque card-with-border per-row treatment.
- **Preview = most recent assistant reply**: `getRowMeta` now picks the latest `role === 'assistant'` message and treats `agent.last_assistant_preview` (server-supplied via `/api/auth/agents`) as the authoritative source. Local session is only preferred when it has a fresher assistant `timestamp` than the server value — covers the moment between live-stream finish and the next `/agents` refresh.
- **Bug fix**: previous version referenced `var(--nm-silicon-soft)`, a token that does not exist. Real name is `--color-silicon-soft`; the bad reference silently fell back to transparent so the selected/unread highlight never painted.

## 2026-05-13 — Phase C: Backend active_run drives the running spinner

`renderAgentStatusIcon` now ORs two signals: the legacy local
``isAgentStreaming(id)`` (per-tab WS state) and a new
``hasBackendActiveRun`` flag derived from ``agent.active_run`` in
the fresh ``/api/auth/agents`` payload. Both call sites (collapsed
icon strip + expanded list) pass ``!!agent.active_run``.

Before this change, "关 tab → 重开 → 啥也没有" was the user-
facing symptom: backend's BackgroundRun was happily writing
event_stream rows but the spinner relied solely on local WS
state, which resets on every tab reload. The new wiring makes the
spinner persist across tabs / reloads / devices — the spinner is
now a function of "the agent has a live BackgroundRun on the
backend right now", which is the truth iron rule #14 establishes.

# AgentList.tsx — Agent CRUD with real-time streaming + completion badges

## 为什么存在

The agent list is the primary navigation for multi-agent concurrent chat. It shows which agents are currently running (spinner), which have completed since you last viewed them (glowing dot badge), and lets you create, rename, delete, and toggle public/private.

## 上下游关系
- **被谁用**: `Sidebar`.
- **依赖谁**: `useConfigStore` (agents, agentId, setAgentId), `useChatStore` (isAgentStreaming, completedAgentIds, setActiveAgent, clearAgent), `api`.

## 设计决策

`completedAgentIds` in `useChatStore` tracks agents that have finished since you last visited them. Selecting an agent clears its completion badge via `setActiveAgent`. The badge is a small glowing dot overlaid on the agent icon.

Collapsed mode shows max 4 agents as icon squares — the rest are invisible but still selectable if you expand the sidebar.

Inline rename: clicking the pencil enters editing mode on that agent row. Enter/Escape confirms/cancels. The `editingAgentId !== agentId` guard ensures you cannot edit a different agent while the current one is selected for rename.

### Team management 双入口（2026-05-09）

`AgentList` 自己也渲染一个 `TeamManagementModal`（除了 `TeamFilterBar` 那一份），通过 `Users2` icon 按钮打开。两份 modal 各持本地 state，互不干扰，因为 modal 内部读的是 `useTeamsStore`，关闭时 store 已经刷新过，再开任一入口都能看到最新数据。

为什么要两个入口：用户反馈 TeamFilterBar 的齿轮 (`Settings2`) 看起来像"系统设置"，发现不到团队管理。`AgentList` 顶部 `[+ Plus]  [Users2]  [↻ Refresh]` 三联工具按钮把"建 agent / 管 team / 刷新"放成一个动作组，让批量管理离 agent 操作只差一个 icon 距离。

## 新人易踩的坑

`handleSelectAgent` always navigates back to `/app/chat` if the user is on a sub-page (Settings, System). This is intentional — clicking an agent always means "go talk to this agent".

Delete hits `api.deleteAgent` which cascades all related DB data server-side. There is no undo.

## 2026-07-22 — team clear-data (mirrors agent clear-data)

Owns `clearTeamTarget` / `clearTeamBusy` + `doClearTeamData` (calls `api.clearTeamData` →
`DELETE /api/teams/{id}/data`, then requestHistoryRefresh on chat scope) and renders
[[ClearTeamDataDialog]] — the exact pattern as the agent [[ClearAgentDataDialog]] path.
TeamChatRow's `onClearData` opens it. Backend: [[teams]] `_wipe_team_data`.
