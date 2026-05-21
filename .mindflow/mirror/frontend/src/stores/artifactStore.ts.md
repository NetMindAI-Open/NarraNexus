---
code_file: frontend/src/stores/artifactStore.ts
last_verified: 2026-05-19
stub: false
---

## 2026-05-19 — quotaError state removed + ECharts LRU added

Two related changes around the artifact pipeline cleanup (see
[[artifact_runner.py]] 2026-05-19 note):

- `quotaError` / `setQuotaError` are gone with the per-user quota. Tool-call
  failures during register_artifact now surface only as a structured
  `{error, code}` returned to the agent; no more layout-level modal.
- New `chartLruOrder: string[]` (newest first, length ≤ `CHART_LRU_LIMIT = 5`)
  + the `_promoteChartLru` helper. Every code path that flips
  `activeArtifactId` pipes the new id through `_promoteChartLru` so an
  echarts artifact rises to the head of the list. `ArtifactColumn` then
  keeps every id in that list mounted with `display: none` for the
  non-active ones, so re-selecting a recent chart is instant — no fetch,
  no `echarts.init`. Tail drops trigger ChartRenderer unmount which runs
  `chart.dispose()`. HTML / CSV / Markdown / PDF / image kinds slide
  through unchanged.

## 2026-05-14-r3 — `delete` no longer takes `deleteSource`

Deletion is registry-only; `delete(agentId, artifactId)` is the entire
surface. Workspace files are never touched. See the artifactsApi mirror
md for the rationale.

## 2026-05-14 — `delete` accepts `deleteSource`

Spec: `reference/self_notebook/specs/2026-05-14-artifact-pointer-model-design.md`

`delete(agentId, artifactId, deleteSource?: boolean)` now passes the popup
choice down to `artifactsApi.remove`. `false` (default) tears down the DB
row only; `true` also `rmtree`s the artifact root directory in the agent
workspace. The popup itself lives in `ArtifactTabStrip` / `ArtifactsSection`
and decides which value to pass.

## 2026-05-08-r3 — WS subscription removed

`_ws`, `connectWs`, and `disconnectWs` have been removed from the store.
The dedicated `/ws/artifacts/{agentId}` endpoint was dropped because the
in-process `ArtifactEventBus` never delivered events across the MCP-process /
FastAPI-process boundary. Artifact signals now arrive exclusively through the
chat WebSocket stream (`tool_output` frames parsed in `ChatPanel.tsx`). The
store retains all REST-backed methods (`loadForSession`, `loadPinned`, `upsert`,
`remove`, `pin`, `delete`) — these are called by the chat WS handler and UI
components as before.

# artifactStore.ts — Artifact tab list, active selection, and collapse state

## Why it exists

The artifact panel is a persistent column alongside the chat panel. Its state (which artifacts are visible, which one is active, whether the column is collapsed) must survive component remounts and respond to server-push events without requiring a user action. A Zustand store outside the React tree is the right place for this.

## Upstream / Downstream

Reads from `services/artifactsApi.ts` for all HTTP operations. Subscribes to the backend WebSocket endpoint `ws://…/ws/artifacts/{agentId}` for real-time push events (artifact created, updated, pinned, deleted).

Consumed by (future) `ArtifactColumn.tsx` and `ArtifactViewer.tsx`. The `collapsed` field drives the CSS split-pane layout; `artifacts` drives the tab list; `activeArtifactId` drives which viewer is rendered.

## Design decisions

**Single flat `artifacts[]` list, not a session-keyed map.** Unlike `chatStore`, which needs per-agent independent streams, the artifact column is always scoped to one agent at a time (the currently open agent). A flat list is simpler and directly maps to "tabs rendered left to right."

**Pinned artifacts merged at the front of the list.** `loadForSession` fetches both pinned and session artifacts, deduplicates (pinned wins position), and merges them. This means pinned artifacts always appear first in the tab bar, regardless of when they were created.

**`upsert` auto-activates new artifacts.** When the WS fires `artifact.created`, `upsert` detects `idx === -1`, prepends the artifact, and sets it as active. When `artifact.updated` fires, `upsert` finds the existing entry and replaces it without changing `activeArtifactId`. This single method handles both cases with one conditional.

**`collapsed` persisted to `localStorage`.** User preference for panel collapse should survive page reload. The store initializes from `localStorage` (with a try/catch for SSR safety) and writes back on every `setCollapsed`. The key is `artifact_column_collapsed` and the value is `'1'` / `'0'` (string, for compatibility with `localStorage`'s string-only storage).

**WS lifecycle owned by the store, not a React hook.** `connectWs` and `disconnectWs` live in the store so that non-React code (e.g., a router-level effect) can manage the connection without needing a mounted component. The WS reference is stored as `_ws` (underscore prefix = internal, not for external consumers).

**`artifact.pinned` WS event mutates `session_id` to `null` when pinning.** When the backend pins an artifact it removes its session affiliation. The WS handler reflects this by setting `session_id: null` on the local copy when `evt.pinned` is truthy. Unpin restores the field from the backend's next PATCH response via `upsert` (the store does not reconstruct `session_id` on unpin from the WS event alone — `upsert` replaces the entry cleanly after the REST call).

## Gotchas

**`remove` reads `activeArtifactId` twice.** The remove action calls `get().activeArtifactId` inside the `set()` argument. Because Zustand's `set` is synchronous and `get()` reflects the current committed state (not the in-flight update), the second read of `activeArtifactId` inside the `set` argument may be stale if the artifact being removed is simultaneously set as active by another call. In practice this race does not occur because WS events are single-threaded in the browser.

**WS `artifact.deleted` calls `get().remove(evt.artifact_id)` even if the artifact was never loaded.** `remove` filters a non-existent ID safely (the filter returns the original list), so this is a no-op. No error.

**`disconnectWs` nulls out `onmessage` and `onclose` before calling `ws.close()`.** This prevents the `onclose` handler from writing `{ _ws: null }` to the store again after `disconnectWs` has already done so — avoiding a redundant state update and a potential double-close race.

**No reconnect logic.** If the WS drops mid-session (network blip), the store ends up with `_ws: null` and stops receiving push events. The next `connectWs(agentId)` call (triggered by the consumer when it detects stale state, or by a component re-mount) re-establishes the connection cleanly.
