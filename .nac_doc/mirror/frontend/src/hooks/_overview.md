---
code_dir: frontend/src/hooks/
last_verified: 2026-04-10
stub: false
---

# hooks/ — Cross-cutting React hooks

## Directory role

Custom hooks that abstract logic used across multiple components. None of these hooks own UI — they exist to separate scheduling, side-effect, and state-derivation concerns from render code. The naming convention follows React's `useFoo` standard.

## Key file index

| File | Responsibility |
|------|---------------|
| `useAutoRefresh.ts` | Tiered background polling (10s inbox, 30s jobs/awareness, 15s cross-agent message detection). Returns `refreshAll()`. |
| `useWebSocket.ts` | Thin React wrapper around `wsManager`. Provides `run`, `stop`, `close`, and `isLoading`. |
| `useTheme.ts` | Light/dark/system theme toggle with localStorage persistence and OS media-query listener. |
| `useTimezoneSync.ts` | One-shot sync of browser timezone to backend on login. |
| `useSkills.ts` | TanStack Query hooks for Skills CRUD (list, install, toggle, remove, study). |
| `index.ts` | Barrel export (excludes `useSkills` — imported directly by `SkillsPanel`). |

## Collaboration with other directories

`useAutoRefresh` orchestrates `preloadStore` refresh methods and reads `chatStore` + `configStore` state. `useWebSocket` delegates entirely to `services/wsManager.ts` — it is just a React adapter. `useTheme` is self-contained (localStorage only). `useTimezoneSync` calls `api.updateTimezone`. `useSkills` uses TanStack Query against `api.*` endpoints and is the only hook in this directory to use a query client (not Zustand).
