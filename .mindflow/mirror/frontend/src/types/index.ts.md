---
code_file: frontend/src/types/index.ts
last_verified: 2026-06-24
stub: false
---

# types/index.ts — the central barrel that re-exports every frontend type module

## Why it exists

So consumers import from one stable path (`@/types`) instead of reaching into
individual files like `@/types/api` or `@/types/you`. The barrel is the public
seam: components depend on `@/types`, and which file a given interface actually
lives in stays an internal detail that can be reorganised without touching call
sites.

## How it works / design

- Pure re-export surface (`export * from './…'`) — it contains no type
  definitions of its own and intentionally so; the real shapes live in the
  member modules ([[api.ts]] for backend response models, [[you.ts]] for the
  owner-scoped "You" workspace aggregates, plus messages / jobComplex / skills /
  platform / teams). Document those files for intent, not this barrel.
- Consumed everywhere the frontend talks to the API or renders agent state;
  e.g. [[DashboardSummary.tsx]] and [[healthColors]] pull `AgentStatus` /
  `AgentHealth` / `AgentKind` through here.
- Gotcha: because every member is `export *`, two modules exporting the same
  name collide silently-then-loudly (TS2308 duplicate-export). Keep names
  unique across the barrel members. Adding a new `types/<x>.ts` only becomes
  reachable via `@/types` once a line is added here — easy to forget.
