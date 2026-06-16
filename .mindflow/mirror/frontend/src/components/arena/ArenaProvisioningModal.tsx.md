---
code_file: frontend/src/components/arena/ArenaProvisioningModal.tsx
last_verified: 2026-06-16
stub: false
---

# ArenaProvisioningModal.tsx — the "Setting up your Arena agent…" toast

## Why it exists

The non-blocking progress UI for the Arena landing flow. Mounted once near the
top of `App.tsx` (beside the banners); the full app renders underneath. It only
communicates progress so a user arriving from arena42.ai isn't staring at a bare
spinner while their agent is provisioned.

## Upstream / Downstream

**Reads:** `arenaLandingStore` (`status`, `arenaName`, `error`). **Driven by:**
`lib/arenaLanding.ts`. Renders nothing when `status === 'idle'`.

## Design decisions

- A top-centred, `pointer-events-none` toast (glassy card) — does not block the
  app. `provisioning` shows a spinner + label; `ready` shows "✓ {gamertag} is
  ready — opening…"; `error` shows a retry hint.
- Auto-dismiss via a `useEffect` timer (reset after ~1.8s on ready, ~4s on
  error). UI strings are English (铁律 #1).
