---
code_file: frontend/src/hooks/useAgentImported.ts
last_verified: 2026-07-30
stub: false
---

# hooks/useAgentImported.ts — shared post-import side effect

## Why it exists

Every migration entry point (the sidebar "+" Import in [[AgentList]], the
guided-flow welcome modal in [[MigrationGuide]]) needs the same post-apply
wiring: refresh the agent list, select the new agent, navigate to its chat.
The two call sites had byte-identical copies that could drift — this hook is the
single definition. Reads stores via `getState()` so it depends only on `navigate`.
