---
code_file: frontend/src/pages/ManageAgentsPage.tsx
last_verified: 2026-05-08
stub: false
---

# ManageAgentsPage.tsx — Batch agent management (议题 8.B replacement)

议题 8.B decided NOT to implement "undo import" — instead users use the
existing single-agent delete path, but at scale that's painful. This page
is the batch surface that fills the gap.

## Features

- Shift-click range selection
- Filters: text search, by team, special "From bundles" (agents that are
  members of any team with `source='bundle'`) — closest analogue to
  "agents I imported recently"
- Bulk delete (cascade via `api.deleteAgent`)
- Bulk add / remove members from a chosen team

## Gotcha

- Bulk delete loops `api.deleteAgent` per row. No transaction. If 5/10
  succeed and 5 fail, the user sees a partial-success alert. The DELETE
  endpoint itself does cascade through narratives / events / instances /
  social entities / workspace, so each row deletion is atomic per-agent.
- "From bundles" filter relies on `team.source === 'bundle'` and the
  agent being a member. If a user manually re-tags an imported agent
  into a `source='user'` team and removes the bundle team, the agent
  drops off this filter.
