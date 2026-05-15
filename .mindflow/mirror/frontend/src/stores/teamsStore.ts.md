---
code_file: frontend/src/stores/teamsStore.ts
last_verified: 2026-05-08
stub: false
---

# teamsStore.ts — Zustand store for subproject 1

State: `teams[]`, `loaded`, plus selector `teamsForAgent(agentId)`.

Actions: `refresh / createTeam / updateTeam / deleteTeam / addMember / removeMember` 都直调 `api.*` 然后 `await get().refresh()`。乐观更新没做 — 一律全量 refetch。规模小，简单胜过乐观更新。

`persist` middleware 缓存到 `narra-nexus-teams` localStorage key。
