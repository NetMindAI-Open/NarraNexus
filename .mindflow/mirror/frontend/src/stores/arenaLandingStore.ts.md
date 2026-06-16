---
code_file: frontend/src/stores/arenaLandingStore.ts
last_verified: 2026-06-16
stub: false
---

# arenaLandingStore.ts — UI state for the Arena landing toast

## Why it exists

Arena provisioning is async (~0.7–2s: a real Arena registration round-trip).
Without this, the page would sit on a blank spinner. This tiny Zustand store
decouples provisioning progress from page render: the app loads normally and a
small non-blocking toast (`ArenaProvisioningModal`) reflects `status`.

## Upstream / Downstream

**Written by:** `lib/arenaLanding.ts` — `setProvisioning()` when the
`POST /api/arena/provision` call starts, `setReady(arenaName)` on success,
`setError()` on failure.
**Read by:** `components/arena/ArenaProvisioningModal.tsx`, which renders the
toast and auto-dismisses (reset) shortly after `ready` / `error`.

## Design decisions

`status: idle | provisioning | ready | error` is the whole surface — no agent
data lives here (the agent list + selection go through `configStore` /
`chatStore`). Kept separate from `configStore` so a transient landing flow never
pollutes persisted session state.
