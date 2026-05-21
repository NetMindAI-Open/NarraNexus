---
code_file: frontend/src/components/layout/AgentList.tsx
last_verified: 2026-05-21
stub: false
---

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
