---
code_file: frontend/src/hooks/useAutoRefresh.ts
last_verified: 2026-08-21
stub: false
---

## 2026-08-21 — tickBgMessages「有新回复」信号只订阅 chat 流

`tickBgMessages` 用 `getSimpleChatHistory(aid, 5)` 尾条时间戳前进当作"该 agent 有新回复"→ 弹 toast +
点亮侧边栏 badge。后端把 A2A/team 活动纳入 simple-chat-history 后,这条流尾部几乎总是活动行,每次
peer/team turn 都会被误判成"回复了你",制造通知疲劳。改成 `getSimpleChatHistory(aid, 5, 0, 'chat')`,
只看真实对话流;活动去 `ChatPanel` 的 inner tab 自己展示。首轮"只记录不通知"语义不变。

# useAutoRefresh.ts — Tiered background polling with Visibility API pause

## 2026-08-14 — Teams join the mid tier, and the scheduler stops requiring an agent

The team rows in the sidebar carry a room-activity mark: a comparison between a
client watermark and a server timestamp that arrives with the TEAM LIST (see
[[unread.ts]]). Nothing refreshed that list on a timer — it was fetched once,
when the sidebar found it unloaded. A room could talk for an hour and the mark
would appear only on the next full page reload, which from the user's side is
indistinguishable from a feature that does not work.

`tickMid` now calls `useTeamsStore.getState().refresh()`, deliberately BEFORE
the agent guard: a team room needs no agent selected, and the sidebar's team
rows exist whether one is or not. For the same reason the scheduler's own guard
relaxed from `!agentId || !userId` to `!userId` — every poll that needs an agent
already checks for one itself, and gating the whole scheduler on a selected
agent left a user sitting in a team room with no background refresh at all.

Behind the visibility guard like everything else, so a hidden tab still issues
zero requests; and on re-focus `tickMid` fires immediately, which is exactly
when the user wants to know what happened while they were away.

After each teams refresh, `notifyWokenRooms` raises a toast for any room that
went from caught-up to talking. Three decisions are load-bearing:

- **Edge, not level.** A toast per new message in a room where six agents answer
  at once is a notification people turn off — and a feature users turn off is
  worse than one that was never built. A room that is already unread stays
  unread until they open it and says nothing more in the meantime.
- **"Never observed" is distinct from "was caught up".** A team created, joined,
  or seen for the first time this session has no prior observation; treating
  that as caught-up would announce a whole backlog the user just gained access
  to, and would make every unread room shout on app start.
- **No route knowledge.** "Is the user reading it right now" is answered by the
  watermark: the open room advances its own every 3s (see [[TeamChatPanel.tsx]]),
  so by the time this 30s tick sees the message it is already read. Same
  question the sidebar dot asks, answered from the same place — one rule, not
  two that can disagree.

## 2026-05-14 — Artifacts join refreshAll (but NOT the timers)

`refreshAll` now also calls `artifactStore.loadPinned(aid)`. Before this,
nothing reloaded artifacts when an agent run finished — the artifact panel
relied entirely on the mid-stream `tool_output` discovery path, which was
itself broken (see `[[output_transfer.py]]`). A finished run now reliably
re-syncs artifacts.

**Deliberately NOT added to any polling tier.** Artifacts are event-driven
— they only change when an agent run creates/iterates one, or the user
manages them in the UI. `refreshAll` (agent-complete) + the mid-stream
discovery path cover the real cases; a blind 30 s artifact poll would just
burn re-renders and risk disrupting a user mid-read for data that didn't
change. The artifact panel also has a manual refresh button
(`[[ArtifactColumn.tsx]]`) as the explicit escape hatch.

## Why it exists

The app needs to stay fresh without hammering the server. Different data has different staleness tolerances: agent inbox messages should update within 10 seconds, while jobs and awareness can tolerate 30-second delays. Additionally, background agents (not currently selected) can complete while the user is on a different tab — their completion must be surfaced as a toast and badge. `useAutoRefresh` handles all of this in one place so individual panels do not need polling logic.

## Upstream / Downstream

Consumes `preloadStore` (`refreshAgentInbox`, `refreshJobs`, `refreshRAGFiles`, `refreshAwareness`, `refreshChatHistory`, `refreshSocialNetwork`) and `configStore` (`agents`, `refreshAgents`). Calls `useChatStore.setState` directly to push toast entries when background message detection finds a new turn.

Used by `MainLayout.tsx` (or the main shell component) — mounted once for the session so timers are not duplicated.

Returns `refreshAll()`, which `ChatPanel.tsx` calls via `onComplete` after an agent finishes streaming to trigger an immediate full reload of all panels.

## Design decisions

**Three separate tiers.** High-freq (10s, `tickHigh`): inbox only — messages are time-sensitive. Mid-freq (30s, `tickMid`): teams, jobs, RAG files, awareness, social network, agent list — changes here matter but are slower-moving. Background message detection (15s, `tickBgMessages`): polls `getSimpleChatHistory` across ALL agents looking for new turns from server-initiated jobs or Matrix messages.

**Visibility API.** All tick functions return early if `document.hidden`. On tab re-focus, `handleVisibilityChange` fires both `tickHigh` and `tickMid` immediately so the user sees fresh data without waiting for the next interval.

**Refs for stale closure safety.** `agentIdRef` and `userIdRef` are kept current on every render. Interval callbacks close over the refs, not the values, so an agent switch does not leave a timer polling the old agent.

**`tickBgMessages` skips streaming agents.** If `isAgentStreaming(aid)` is true, that agent is receiving live updates via WebSocket — no need to poll. This prevents a double-update during active streaming.

**`latestTimestampRef` bootstraps silently.** On the first poll for any agent, the timestamp is recorded but no notification is fired. This prevents spurious toasts when the user first loads the app.

**Rejected: recursive `setTimeout` for each tier.** Would give more precise interval control but adds complexity when resetting on agent switch. `setInterval` is simpler and the jitter (~100ms) is irrelevant for this use case.

## Gotchas

**`refreshAll` calls all domains without `silent=true`.** This means each domain shows loading state and re-renders its panel. Calling `refreshAll` from user interactions (e.g., manual refresh button) is fine; calling it on a fast timer would cause UI flicker. It is intentionally only called once from `onComplete` after streaming ends.

**`tickBgMessages` makes N HTTP calls per tick** (one per agent). For users with many agents this could be significant. The `getSimpleChatHistory` endpoint returns only 5 messages (`limit=5`) to minimize payload, but the number of requests scales with agent count.

**The hook does not restart timers on agent switch.** The `useEffect` dependency array includes `agentId` and `userId`, so the timers are torn down and recreated when the active agent changes. This resets the interval clocks — the user may wait up to 30 seconds for the first mid-freq tick after switching agents, rather than seeing data immediately. `preloadAll` in `MainLayout` handles the initial data fetch on switch; `useAutoRefresh` only needs to handle subsequent background refresh.

## 2026-08-19 — 零 Agent 也刷新侧栏 + 首登快轮询

两处配合服务端自动供给的引导 Agent（登录返回后 fire-and-forget 创建，
前端只能靠拉取发现）：

1. **`tickMid` 里 `refreshAgents()` 移到 agent guard 之前**。此前零 Agent
   用户（无 agentId）的 mid tick 在 guard 处早退，侧栏永不更新——引导
   Agent 只有手动刷新页面才出现。代价是"已登录但未选中 Agent"的用户每
   30s 多一次 /api/auth/agents，这是有意为之（注释已写明，防止后人把
   guard 移回去）。
2. **新增首登快轮询 effect**：`isGuideCoachmarkPending()` 且 agents 为空
   时，每 2s 拉一次 agent 列表、上限 10 次（~20s），拉到或超时即停；
   `document.hidden` 时跳过请求但照常计数（上限是**墙钟** ~20s，不是前台
   时间——后台标签页不会让 interval 无限存活；与三个 tick 同一条"后台零
   请求"纪律）。
   上限是承重的——/api/auth/agents 带 active-run + last-message 富化，
   不允许无限期快轮。快轮询有专属测试（armed→2s 内拉到 / 超时封顶 /
   未 armed 不快轮）。

对应测试更新：useAutoRefresh.teams.test.ts 的「no agent selected」用例
从"断言 refreshAgents 不被调"反转为"必须被调"（旧断言钉的正是这个盲区）。
