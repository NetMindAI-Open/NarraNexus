---
code_file: frontend/src/hooks/useSkillMarketplace.ts
last_verified: 2026-07-21
stub: false
---

# useSkillMarketplace.ts

TanStack Query hooks for the marketplace: search (expects the DEBOUNCED
string as input — debouncing lives in the component), detail, install
mutation (invalidates 'skills' + 'skill-marketplace' keys), and
useSkillUpdates (5min staleTime — update checks are cheap but not free).
Follows useSkills.ts conventions: agentId from useConfigStore, enabled
guards on identity.
