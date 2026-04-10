---
code_file: frontend/src/stores/preloadStore.ts
last_verified: 2026-04-10
stub: false
---

# preloadStore.ts — Parallel panel data cache with silent refresh

## Why it exists

Without this store, each panel (Jobs, Awareness, Inbox, Social Network, etc.) would independently fetch its own data when the user navigates to it, causing visible loading spinners on every tab switch. `preloadStore` loads all panel data in parallel on app init so tabs open instantly. It is the shared data layer for everything that is not real-time streaming.

## Upstream / Downstream

`preloadAll` fans out to seven concurrent API domains: `api.getAgentInbox`, `api.getJobs`, `api.getAwareness`, `api.getSocialNetworkList`, `api.getChatHistory`, `api.listRAGFiles`, `api.getCosts`. Each resolves independently — the fast calls (awareness ~2ms) update the UI before the slow ones (chat history which may be megabytes) complete.

Consumed by nearly every panel component: `AgentInboxPanel.tsx`, `JobsPanel.tsx`, `AwarenessPanel.tsx`, `RAGUpload.tsx`, `RuntimePanel.tsx`, `CostPopover.tsx`. Also used by `useAutoRefresh.ts`, which calls the individual `refresh*` methods on a timer.

`preloadAll` is triggered from `MainLayout.tsx` (or wherever the main shell mounts) whenever `agentId` or `userId` changes.

## Design decisions

**`loadDomain` generic helper.** All seven domains follow the same pattern: set loading flag, call fetcher, on success update data, on failure set error. Rather than repeating that pattern seven times, a single `loadDomain<T>` function encapsulates it. The `silent` flag controls whether loading state is toggled and whether errors are surfaced.

**Silent mode skips set() when data is unchanged.** In `silent=true` mode, the helper serializes both old and new values to JSON and skips `set()` if they match. This prevents pointless re-renders during background polling — components only re-render when data actually changes.

**`preloadAll` deduplicates by `(agentId, userId)`.** If `preloadAll` is called again with the same pair and `jobs.length > 0`, it returns immediately. This prevents double-fetching when `MainLayout` re-renders due to unrelated state changes.

**`_inboxLimit` remembers user's "load all" choice.** When the user explicitly loads all inbox messages (`limit=-1`), that preference is stored in `_inboxLimit` and applied on subsequent auto-refreshes. `limit=0` resets the preference back to default.

**`addChatHistoryEvent` for optimistic insertion.** After the agent completes a turn, the new event is appended locally and re-sorted by timestamp, giving instant feedback without waiting for the next full `refreshChatHistory`.

**Rejected: per-panel stores.** Would eliminate the "big ball of loading flags" but would require each panel to independently manage polling, deduplication, and silent refresh. The centralized approach makes `useAutoRefresh` simpler.

## Gotchas

**`preloadAll` guard checks `jobs.length > 0`.** If the first load returned an empty jobs list for a valid agent, subsequent `preloadAll` calls will re-fetch. This is intentional (the agent may not have jobs yet), but it means "no jobs" does not count as "already loaded". A fresh agent will trigger preloadAll on every render until some state outside jobs changes.

**`refreshJobs` ignores `_userId` argument.** The signature includes `_userId?` for backward compatibility but the implementation uses the `agentId` only (backend infers user from the agent). Passing a `userId` here has no effect.

**Large chat history.** `api.getChatHistory` can return megabytes for active agents. The `silent` JSON comparison in `loadDomain` serializes the entire payload on every background poll. For very large histories this could be slow. The current approach is "good enough" but would need a smarter diff strategy if histories grow to hundreds of events.

**`clearAll` does not cancel in-flight requests.** If `preloadAll` is in progress and `clearAll` is called (e.g., on logout), the in-flight promises will still resolve and call `set()` with data from the old session. The `(agentId, userId)` deduplication guard in `preloadAll` does not prevent this because `lastAgentId` has already been cleared. In practice the window is small and the worst case is a stale flash of the previous agent's data.
