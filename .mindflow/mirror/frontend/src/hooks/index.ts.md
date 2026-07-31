---
code_file: frontend/src/hooks/index.ts
last_verified: 2026-07-30
stub: false
---

# index.ts — Hooks barrel export

## Why it exists

Provides a single import path `@/hooks` for the four hooks used across multiple components: `useTheme`, `useAgentWebSocket`, `useTimezoneSync`, and `useAutoRefresh`.

## Notes

`useSkills` is intentionally not re-exported here — it is only used inside the Skills panel and is imported directly from `@/hooks/useSkills`. Adding it to the barrel would not be harmful but would suggest it is more widely shared than it is.

## 2026-06-10

Added `useBookmarkSignals` export ([[useBookmarkSignals]]).

## 2026-07-30

Added `useAgentImported` export ([[useAgentImported]]) — the shared post-import
side effect used by [[AgentList]] and [[MigrationGuide]].
