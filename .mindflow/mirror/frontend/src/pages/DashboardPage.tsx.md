---
code_file: frontend/src/pages/DashboardPage.tsx
last_verified: 2026-06-24
stub: false
---

> 2026-06-24: Renamed from the mis-named `DashboardPage.md` to the canonical
> `DashboardPage.tsx.md` and rewritten in English to house format. Behavior is
> unchanged — still the polling FSM dashboard mounted at `/app/dashboard`.

# DashboardPage.tsx — Agent Dashboard v2: a self-throttling polling status board

## Why it exists

The cross-agent operations view (`/app/dashboard`): a card grid showing every
agent's run health at a glance, separate from any single agent's chat. It is the
one screen that has to keep itself fresh without a websocket, so its real job is
to poll the backend status endpoint at a rate that adapts to whether the user is
actually looking — cheap when hidden/idle, responsive when focused.

## How it works / design

- **Polling FSM lives in [[dashboardStore]], not here.** The page is a thin view:
  it feeds the store the FSM inputs (`visibility` from the `visibilitychange`
  event, `tauriFocused` from Tauri `tauri://blur` / `tauri://focus`) and runs a
  self-rescheduling `tick()` whose next delay is `store.computeInterval()`. The
  store decides cadence from `visibility × tauri-focus × any_running`; an interval
  of `Infinity` parks the loop entirely (e.g. tab hidden, nothing running).
- **Tray badge is a side effect of polling.** After each successful fetch the
  page computes the running count and calls `setTrayBadge(running)` only when it
  changed (Tauri desktop; web mode is a no-op). This keeps the dock/tray count
  live without a separate loop.
- **429 is handled as backpressure, not an error.** A 429 routes to
  `store.onRateLimited()` (exponential backoff via `computeInterval`) instead of
  the red error banner; other failures go to `onFetchError`.
- **Upstream/downstream**: subscribes to [[dashboardStore]]; renders
  [[DashboardSummary]] (health legend/counts) over a grid of [[AgentCard]] (each
  card owns its own expand/collapse). Data via `api.getDashboardStatus`; tray via
  `lib/tauri`.
- **Gotchas**: the cleanup must set `active=false` AND `clearTimeout` or a stale
  `tick` keeps firing after unmount. `listenTauri` returns null off-desktop, so
  unlisten with `unlistenFn?.()`. One page-level `expandedId` means a single card
  expands at a time (a `Set` would be needed for multi-expand). `DashboardSummary`
  counts are aggregated frontend-side — public agents are forced to
  `healthy_idle` since they carry no `health`; if the backend ever adds health to
  public agents, fix the aggregation too.
