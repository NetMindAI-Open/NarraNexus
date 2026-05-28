---
code_file: frontend/src/components/UpdateBanner.tsx
last_verified: 2026-05-27
stub: false
---

# UpdateBanner.tsx — Global "Update ready" pill (ChatGPT-style)

## Why it exists

One of three UI surfaces for the unified auto-updater (see
[[updater.rs]] for the state machine + [[updaterStore.ts]] for
the mirror). This is the **least-intrusive** surface: a small
floating pill at the top-center of the app, visible from every
screen, surfaced only when there is an action the user can take.

## Render gate

**Only renders when `state.kind === "ready"`.** Specifically NOT:
- `available` / `downloading` / `installing` — these phases run
  silently per Owner spec (Q1=A, background download). A banner
  during a 5-minute 400 MB download would be noise.
- `failed` — global error banners over network blips train users
  to dismiss without reading. Failures are surfaced in Settings
  only, where the user has gone looking.
- `up_to_date` / `idle` / `checking` — never user-facing here.

## Layout

Floating pill at `top: 12px`, centered, `z-50` (above the app
chrome but below modals). Wrapper has `pointer-events: none` and
the pill itself overrides to `pointer-events: auto` — clicks
outside the pill pass through to the underlying UI so the banner
never blocks the workspace.

Three children:
1. `Download` icon — visual anchor
2. Text — "NarraNexus **vX.Y.Z** downloaded and ready"
3. `Restart now` button → `restartForUpdate()` ([[tauri.ts]])
4. `×` dismiss → session-scoped (state cleared on page reload)

## Dismiss semantics

Session-scoped via `useState<string | null>`. Dismiss key is
`ready:${version}`, so:
- User dismisses 1.7.10 → no banner for the rest of this session
- A newer 1.7.11 finishes downloading later → new key, banner re-arms
- User reloads the page → banner re-shows (intentional —
  conservative tradeoff: easier to dismiss again than to miss an
  update sitting Ready)

## Mounted by

[[App.tsx]] — once at the root, outside the router so it shows
across all routes.
