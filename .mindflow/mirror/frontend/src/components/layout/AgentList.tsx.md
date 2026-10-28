---
code_file: frontend/src/components/layout/AgentList.tsx
last_verified: 2026-09-11
stub: false
---

## 2026-09-11 — hosts the agent row ⋯ menu (OWNER-REQUIRED, reinstated after #383)

AgentList passes `rowActions` to the flat AGENTS [[AgentGroupSection]]:
Rename → `useAgentActions().renameAgent` (current `api.updateAgent`, list
refresh, clash / identity-memory report), Delete → `useAgentActions().deleteAgent`
(confirm dialog through this component's existing `useConfirm` host, current
`api.deleteAgent`, list refresh, cached session dropped, active agent
re-pointed when it was the deleted one) and then `navigate('/app/dashboard')`
ONLY when the deleted agent was the active one — the same landing as the
profile page; deleting some other agent leaves the current view alone.
Model & framework opens an `AgentLlmConfigPanel` for that row (`configAgentId`
state), `onSaved` refreshes the agent list (which also refreshes the composer
model chip, see [[../chat/ComposerModelBadge]]). Clear data and the description
editor stay on the profile page only. Owner-required entry — keep it.

## 2026-09-03 — 零 agent 空态 CTA 改为进创建分叉页

`handleCreateAgent` 从 `await createAgent()` 改为导航到
[[ChooseCreateMethodPage.tsx]]。这个空态服务的正是首次使用者 —— 分叉页存在的
理由就是他。

`handleCreateAgentInTeam` **没有**改道：团队内创建已经有明确目的地（团队群聊）
和明确意图，不需要再问一次「你想从哪里开始」。

## 2026-09-03 — 团队「清理数据」对话框与状态移出

`clearTeamTarget`/`clearTeamBusy`/`doClearTeamData`/`ClearTeamDataDialog` 及
`onClearData` 透传全部删除;`requestWorkspaceRefresh` 不再从 store 解构。
逻辑整体搬到 [[../chat/team/TeamManagePanel.tsx]](调用)+ [[../chat/team/TeamChatPanel.tsx]]
的 `handleCleared`(刷新)。agent 行的清理不受影响。

## 2026-08-27 — agent 行的所有 mutation 搬走,本文件只剩编排

Owner 要求去掉侧边栏 chat 行的 ⋮。连锁结果:`handleStartEdit` /
`handleEditAgent` / `doEditAgent` / `handleSaveEdit` / `handleCancelEdit` /
`handleDeleteAgent` / `handleClearData` / `doClearData` /
`handleTogglePublic` 全部没有调用点,一并删除;`ClearAgentDataDialog` 的
宿主移交 [[../../pages/AgentProfilePage.tsx]],`EditAgentDialog` 整个组件
连同测试删除(它唯一的入口就是那个 kebab,而名称 + 描述编辑在 profile 页
Settings tab 已有完整归宿)。`SHOW_AGENT_PUBLIC_TOGGLE` 这个 flag 也跟着
删掉——它唯一的作用是控制 kebab 里那个 Globe/Lock 项;后端接口没动,真要
重开 public 功能是在 profile 页重新接一次,不是翻这个 flag。

**08-19 那条"改名的两个成功但你得知道"没有被丢掉**:
`warnAboutUpdateSideEffects` 搬到了 profile 页(改名后 `res.name_clash_with`
/ `identity_record_updated === false` 仍然会弹),i18n key 从
`layout.editAgentDialog.rename*` 重命名为 `layout.agentRename.*`,10 个语言
文件同步。删掉入口时把它带走的静默回归,是这次唯一必须防的东西。

TEAMS 行的 [[TeamRowMenu.tsx]] ⋮ **保留**:team 没有 profile 页,那几个
操作没有别处可去。

## 2026-08-19 — 改名的两个"成功但你得知道"报给用户了

后端在改名时会算出两件**不是失败**的事,所以它们永远不会进错误分支——而三个改名
调用点原来只读 `res.success`,于是**界面成了唯一让这两件事静默发生的入口**(后端
注释当时已经写着"不再静默",前端却没接,独立审查第七轮抓的):

- `name_clash_with` —— 同 owner 下已经有别的 agent 叫这个名字。**不拦**(把名字从
  一个 agent 转给另一个是 owner 会故意做的),但两个 agent 同应一个名字正是深圳
  P1 的起点。
- `identity_record_updated === false` —— 名字**已经存进去了**,但 agent 的身份
  记忆没能更新,它可能继续自述旧名。这就是事故本体的状态。

`warnAboutUpdateSideEffects(res)` 一个函数、三处调用(is_public 切换、编辑弹窗、
行内改名)。**故意抽成一处**:三份拷贝就是第四个调用点漏掉提示的方式——这次改动
本身就在这个坑里栽了两回(函数写了没接、接了一处漏另一处)。

放在 `refreshAgents()` 之后:列表先变成服务端真相,再弹提示,否则用户在读提示时
背景里的名字还是旧的。

i18n 只加了 en / zh —— `layout.editAgentDialog` 这一节在其余 8 个语言文件里本来
就不存在,走回退,与 `toastDiskWarn` 同一惯例。

## 2026-08-18 — merge ruling: team 行未读圆点不接线（下方 08-14 条 UI 半边失效）

Owner ruled v4 行尾结构（成员数 → ⋮）胜出，dev 的未读圆点 + 「谁说了什么」
第二行**不接线**：本文件不再向 [[TeamChatRow]] 传 `unread/preview/authorName`，
`teamUnread` 判定函数随之移除。dev 的 `teamUnreadBadge.test.tsx` 随 UI 一起
删除——圆点回归时从 dev 恢复它。

保留的半边：`markTeamRead` 水位线 effect 原样保留（打开房间即记已读，离开后
仍是清的），[[useAutoRefresh]] 里的 `teamHasUnread` toast 判定也仍然在用。
所以 08-14 条里 UI 渲染的描述已失效，水位线/useAutoRefresh 的描述仍准确。

## 2026-08-17 — 改名成功后回读列表，别让手打的补丁成为持久化的那份

`doEditAgent` / `handleSaveEdit` 原来只做乐观更新：`setAgents(rawAgents.map(...))`
把响应里的 `name` 拍进本地数组就完事。两个问题：

1. 写的是 `name: res.agent?.name`——`res.agent` 虽然在 `if` 里判过真，但字段本身
   可能缺，于是整行的名字被写成 `undefined`。现在是 `?? a.name`，缺字段就保留原值，
   不把行标题清空。
2. 更要紧的：`configStore.agents` 是 persist 到 localStorage 的且没有
   `partialize`（见 [[configStore.ts]]），所以**手打的那份补丁就是下次页面加载
   显示的那份**。它与服务端真值之间任何差异（后端做了 trim / 长度截断 / 归一化）
   都会以「改名后来又变回去了」的形态出现。所以成功之后 `await refreshAgents()`
   ——即工单要求的「失效 agent 列表/详情缓存」。乐观更新保留，给即时反馈；
   回读负责对账。

`name` 与 `description` 取响应的方式**故意不同，别去「统一」它们**：
`description` 直接取服务端值（`?? ''`）——响应总是带这个 key，清空后回的是
`''`（已实测；只有从没设过的行才回 `null`），这里回退到本地值等于把用户刚删掉
的文字放回持久化 store。`name` 保留本地兜底，因为它是行标题，写成 `undefined`
会让整行退回显示裸 `agent_id`。两支各留一句注释就是为了防止下一个人合并它们。

`handleTogglePublic` 是 `api.updateAgent` 的第三个调用点，一并对齐（成功后
`refreshAgents()`、失败弹 alert）。它今天走不到——`SHOW_AGENT_PUBLIC_TOGGLE`
是 false——但那个 flag 的注释自己写着「翻回来只要一行」，翻回来的那一刻本条修掉
的两个缺陷会在这个入口原样复活，所以现在就对齐比留给未来便宜。

第三件：inline 改名（`handleSaveEdit`）失败时原来只 `console.error`，用户那一侧
**什么都没有**——输入框收起、行标题弹回旧名，和「平台把我的编辑丢了」无法区分，
于是继续重试。现在与 `doEditAgent` 一致走 `alert(...)`（同一个
`layout.editAgentDialog.saveFailedTitle`），catch 分支同样弹。

侧栏名字只有一个来源：`configStore.agents`（TopBar / ChatPanel / BookmarkStrip /
TeamManagementModal 全是 `agents.find(...)` 解析，team 成员存的是 id 不是名字）。
所以这一条 + [[configStore.ts]] 2026-08-17 条覆盖了前端能显示旧名的全部路径。

工单：深圳线下第二轮 P1。注意**根因不在前端**——见 [[auth.py]] 2026-08-17 条，
后端把已落库的改名报成了失败（rowcount 方言差异），本条修的是同一条链上前端
这一段的两个真实缺陷。

## 2026-08-14 — team 行的未读标记

每个 team 行（展开态和折叠态的头像轨道**都有**：窄窗口下轨道就是整个 sidebar，
只在展开态显示的标记等于用户看不见的标记）现在会在房间说过话之后打一个圆点，并在
第二行显示「谁说了什么」，和它下面的 agent 行一致。

判定是 `teamHasUnread(t.last_message_at, teamId)`（[[unread.ts]]），**当前打开的
那个房间永远不打标记**——正在读的那一行上的标记是噪音。

打开房间会把水位线写进 localStorage，所以**离开之后标记依然是清的**：只在行处于
active 时清掉，正是 agent 徽标在拿到自己的 marker 之前的那个 bug。

另一半在 [[useAutoRefresh]]：team 列表此前只在 sidebar 发现它未加载时取过一次，
没有任何定时刷新。标记只会在整页重新加载后才出现——从用户角度和「这功能没做」
无法区分。

## 2026-08-11 — TEAMS/AGENTS 分组头三角换 lucide

"▶" 字符三角(渲染为实心)→ lucide ChevronRight + rotate-90,与
TeamChatRow / AgentGroupSection 的展开箭头统一线性图标语言
(design_system.md §5)。

## 2026-08-06 — Chat UI v4:回归纯列表

创建 / 导入 / 导出 / manage-agents 入口全部移出(去 Sidebar 全局导航),
`collapsed` prop 与 72px 头像栏代码路径删除(v4 收起=整栏隐藏)。头部只剩
Chats 标签 + 计数 + 搜索(打开 ⌘K palette,uiStore.paletteOpen)+ 刷新。
activitySignature 性能契约、TEAMS/AGENTS 两段式去重结构、unread 逻辑不变。
TeamManagementModal / ImportAgentModal 挂载移交 [[Sidebar.tsx]];
AgentsHeaderMenu 组件删除。

## 2026-07-28 — hosts the Agent Migration entry point ([[ImportAgentModal]])

Owns `importOpen` + `handleImportApplied`. Passes `onImportAgent` into
[[CreateMenu]] (and the collapsed-rail popover) **only when
`useRuntimeStore.mode === 'local'`** — the migration scanner reads the
filesystem and 503s on cloud, so the import item is hidden off-desktop. On a
successful apply, `handleImportApplied` calls `refreshAgents()` then
`setAgentId` / `setActiveAgent` + navigates to `/app/chat` — the same store
wiring as `useCreateAgent`, so imported agents land selected like created ones.

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
hook too). Reason at the time: the onboarding checklist (retired
2026-08-19) also created agents, and both call sites had to share one path
(store wiring + the `first_agent_created` onboarding side effect). See
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

## 2026-08-10 — clearing a team's files also refreshes the workspace panel

`doClearTeamData` already bumped `requestHistoryRefresh()` for the chat scope;
the files scope had no equivalent, so an open team workspace kept listing the
artifacts and files it had just deleted. Now calls
`requestWorkspaceRefresh()` ([[chatStore.ts]]) on that scope.

## 2026-08-11 — 清团队数据的第三个 scope

`doClearTeamData` 的 scopes 增加 `bulletin`；`files || bulletin` 时都触发
`requestWorkspaceRefresh()`，公告栏面板挂在同一个 tick 上，
所以清空后不会留在屏上列已被服务端删掉的规则。