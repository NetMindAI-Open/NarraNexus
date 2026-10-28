---
code_file: frontend/src/platform/registries/when.ts
last_verified: 2026-09-04
stub: false
---

# registries/when.ts — the `when` predicate grammar

## Intent

A closed vocabulary (spec §658) evaluated by the host: `conversationKind:<kind>`, `agentHas:<module>`, `setting:<key>`, each negatable with `!`, a list ANDs. Parsed at registration (`parseWhen`) so a typo is an error, never an entry that is silently always visible; `evaluateWhen` runs against the `WhenContext` the host builds (`platform/whenContext.ts`). Plugins name conditions; they never evaluate them.
