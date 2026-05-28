---
code_file: frontend/src/stores/updaterStore.ts
last_verified: 2026-05-27
stub: false
---

# updaterStore.ts — Zustand mirror of the Rust auto-updater state machine

## Why it exists

The unified updater (see [[updater.rs]]) holds the canonical state
on the Rust side. The frontend needs to:
1. Render that state on **two surfaces** ([[UpdateBanner.tsx]] +
   the Updates section of [[SettingsPage.tsx]]).
2. Avoid each surface independently subscribing to `updater:state`
   (would double-bind on every page change and accumulate listeners).
3. Recover state on cold mount even when a startup-auto pipeline
   already transitioned past `idle` before React attached.

This store is the single bridge: one event subscription, one
fetch-on-mount, both surfaces consume the same Zustand selector.

## API

| field / method | shape | purpose |
|----------------|-------|---------|
| `state` | `UpdaterState` | current mirror of Rust state machine; see [[tauri.ts]] for the union type |
| `init()` | `async () => void` | call once at App mount: fetches current snapshot via `getUpdaterState()`, then subscribes via `listenUpdaterState()` |
| `teardown()` | `() => void` | idempotent unsubscribe; safe in React effect cleanup, robust to dev StrictMode double-mount |
| `setState(s)` | `(UpdaterState) => void` | for tests / explicit override (no consumer uses this in production) |

## Wired by

[[App.tsx]] — `useEffect` on App mount calls `init()`, cleanup
calls `teardown()`. The `initialised` guard inside `init()` makes
double-init a no-op so StrictMode's double-effect doesn't double-
subscribe.

## Read by

- [[UpdateBanner.tsx]] — global top-center pill; renders only on
  `state.kind === "ready"`.
- [[SettingsPage.tsx]] — `UpdatesSection` renders every state with
  full detail (progress bar, install spinner, ready button, failure
  reason).

## Gotcha

`init()` runs `getUpdaterState()` BEFORE `listenUpdaterState()` on
purpose — there is a race where a fast startup-pipeline can
already be in `Downloading` by the time React mounts. If we
subscribed first we would miss the snapshot until the next state
transition; fetching the snapshot first guarantees the store
shows the right thing even at frame 1.

Web/cloud build: `init()` early-returns at `isTauri() === false`,
so the store stays at `{ kind: "idle" }` forever (the banner never
renders, the Settings section shows "Check for updates" disabled
implicitly because clicking it kicks an IPC that's also a no-op).
