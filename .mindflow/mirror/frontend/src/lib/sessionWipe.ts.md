---
code_file: frontend/src/lib/sessionWipe.ts
last_verified: 2026-08-27
stub: false
---

# lib/sessionWipe.ts — the one authoritative "leave this session" wipe

## Why it exists

Extracted from [[Sidebar]] (2026-08-27) when the first-run flow got its own
"log out" affordance. A second hand-rolled half-logout is exactly how cloud data
bled into a later local session before; one function is the only way to keep
them identical.

## Design decisions

- **Store resets AND explicit localStorage removals.** Zustand's persist
  middleware may not have flushed by the time the page reloads, so the
  `removeItem` calls — not the store resets — are what guarantee factory
  defaults on the next load.
- **Callers must follow with a full document load** (`window.location.href`),
  never a soft navigate: soft navigation keeps the React tree,
  closure-captured store snapshots, in-flight fetches and module caches from the
  old session alive.
- `lastSeenAwarenessTime:*` is wiped by prefix because configStore writes those
  keys directly — no store's `clearAll` covers them.

## Gotcha

- This does NOT ask for confirmation. Sidebar's logout confirms first; the
  welcome rail's logout does not (a user who just landed there has nothing to
  lose). The confirmation is the caller's decision, not this function's.
