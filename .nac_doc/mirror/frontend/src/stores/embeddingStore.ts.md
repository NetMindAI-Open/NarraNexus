---
code_file: frontend/src/stores/embeddingStore.ts
last_verified: 2026-04-10
stub: false
---

# embeddingStore.ts — Embedding rebuild status and adaptive polling

## Why it exists

The backend's vector embedding index can become stale after new documents are added. Rebuilding it is an async server-side operation that takes minutes. This store bridges that async operation to the UI: it fetches current status, lets the user trigger a rebuild, and uses adaptive polling to reflect progress without hammering the server.

## Upstream / Downstream

Talks to `api.getEmbeddingStatus()` (`GET /api/providers/embeddings/status`) and `api.rebuildEmbeddings()` (`POST /api/providers/embeddings/rebuild`). These are the only two backend endpoints involved.

Consumed by `EmbeddingStatus.tsx` and `EmbeddingBanner.tsx`. The banner component subscribes to `status` to render a persistent warning when pending documents exist. `SettingsPage.tsx` mounts `EmbeddingStatus` for user-triggered rebuilds.

## Design decisions

**Adaptive polling interval.** When a rebuild is in progress (`status.migration.is_running`), the poll fires every 3 seconds. Once `all_done` and the migration has stopped, `stopPolling()` is called automatically rather than switching to a slow interval. This keeps the UI responsive during rebuilds without wasting requests in the steady state.

**`startPolling` restarts cleanly.** The method clears any existing timer before creating a new one. Calling `startPolling()` twice is safe — the old interval is cancelled.

**Silent failure on `fetchStatus`.** Embedding status is non-critical — if the backend is not yet configured or does not support the endpoint, the error is swallowed to `console.debug`. The component shows nothing rather than an error state.

**`_pollTimer` exposed in state.** Timer IDs live in the store so callers can always call `stopPolling()` from any context without needing a React ref. The underscore prefix signals it is an implementation detail not meant for rendering.

## Gotchas

**`startPolling` reads `status` at call time to decide the interval.** If called immediately after `startRebuild()` (before the fresh `fetchStatus` resolves), `status.migration.is_running` may still be `false` from the previous fetch, causing the initial interval to be 15 seconds instead of 3 seconds. The code works around this by calling `fetchStatus()` inside `startRebuild` before calling `startPolling()`, but the race is narrow.

**`stopPolling` is not called on unmount by any component.** Components that call `startPolling` are responsible for calling `stopPolling` in their cleanup. If they don't (e.g., a component that mounts and never unmounts), polling runs indefinitely until `all_done`. This is acceptable for the current usage patterns but would become a problem if the component is conditionally rendered.
