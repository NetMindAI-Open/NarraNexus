---
code_file: frontend/src/hooks/useAutoRefresh.ts
last_verified: 2026-04-10
stub: false
---

# useAutoRefresh.ts — Tiered background polling with Visibility API pause

## Why it exists

The app needs to stay fresh without hammering the server. Different data has different staleness tolerances: agent inbox messages should update within 10 seconds, while jobs and awareness can tolerate 30-second delays. Additionally, background agents (not currently selected) can complete while the user is on a different tab — their completion must be surfaced as a toast and badge. `useAutoRefresh` handles all of this in one place so individual panels do not need polling logic.

## Upstream / Downstream

Consumes `preloadStore` (`refreshAgentInbox`, `refreshJobs`, `refreshRAGFiles`, `refreshAwareness`, `refreshChatHistory`, `refreshSocialNetwork`) and `configStore` (`agents`, `refreshAgents`). Calls `useChatStore.setState` directly to push toast entries when background message detection finds a new turn.

Used by `MainLayout.tsx` (or the main shell component) — mounted once for the session so timers are not duplicated.

Returns `refreshAll()`, which `ChatPanel.tsx` calls via `onComplete` after an agent finishes streaming to trigger an immediate full reload of all panels.

## Design decisions

**Three separate tiers.** High-freq (10s, `tickHigh`): inbox only — messages are time-sensitive. Mid-freq (30s, `tickMid`): jobs, RAG files, awareness, social network, agent list — changes here matter but are slower-moving. Background message detection (15s, `tickBgMessages`): polls `getSimpleChatHistory` across ALL agents looking for new turns from server-initiated jobs or Matrix messages.

**Visibility API.** All tick functions return early if `document.hidden`. On tab re-focus, `handleVisibilityChange` fires both `tickHigh` and `tickMid` immediately so the user sees fresh data without waiting for the next interval.

**Refs for stale closure safety.** `agentIdRef` and `userIdRef` are kept current on every render. Interval callbacks close over the refs, not the values, so an agent switch does not leave a timer polling the old agent.

**`tickBgMessages` skips streaming agents.** If `isAgentStreaming(aid)` is true, that agent is receiving live updates via WebSocket — no need to poll. This prevents a double-update during active streaming.

**`latestTimestampRef` bootstraps silently.** On the first poll for any agent, the timestamp is recorded but no notification is fired. This prevents spurious toasts when the user first loads the app.

**Rejected: recursive `setTimeout` for each tier.** Would give more precise interval control but adds complexity when resetting on agent switch. `setInterval` is simpler and the jitter (~100ms) is irrelevant for this use case.

## Gotchas

**`refreshAll` calls all domains without `silent=true`.** This means each domain shows loading state and re-renders its panel. Calling `refreshAll` from user interactions (e.g., manual refresh button) is fine; calling it on a fast timer would cause UI flicker. It is intentionally only called once from `onComplete` after streaming ends.

**`tickBgMessages` makes N HTTP calls per tick** (one per agent). For users with many agents this could be significant. The `getSimpleChatHistory` endpoint returns only 5 messages (`limit=5`) to minimize payload, but the number of requests scales with agent count.

**The hook does not restart timers on agent switch.** The `useEffect` dependency array includes `agentId` and `userId`, so the timers are torn down and recreated when the active agent changes. This resets the interval clocks — the user may wait up to 30 seconds for the first mid-freq tick after switching agents, rather than seeing data immediately. `preloadAll` in `MainLayout` handles the initial data fetch on switch; `useAutoRefresh` only needs to handle subsequent background refresh.
