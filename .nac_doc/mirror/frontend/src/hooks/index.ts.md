---
code_file: frontend/src/hooks/index.ts
last_verified: 2026-04-10
stub: false
---

# index.ts — Hooks barrel export

## Why it exists

Provides a single import path `@/hooks` for the four hooks used across multiple components: `useTheme`, `useAgentWebSocket`, `useTimezoneSync`, and `useAutoRefresh`.

## Notes

`useSkills` is intentionally not re-exported here — it is only used inside the Skills panel and is imported directly from `@/hooks/useSkills`. Adding it to the barrel would not be harmful but would suggest it is more widely shared than it is.
