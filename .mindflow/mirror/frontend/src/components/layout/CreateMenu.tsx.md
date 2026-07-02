---
code_file: frontend/src/components/layout/CreateMenu.tsx
last_verified: 2026-06-23
stub: false
---

# layout/CreateMenu.tsx — The "+" create dropdown (Agent / Team)

## Why it exists

Surfaces teams as a first-class creatable object alongside agents (the
homepage's team-first model). Replaces the former single create-agent "+"
button in [[AgentList]]'s header with a two-item dropdown: **Create Agent**
(the existing `useCreateAgent` flow) and **Create Team** (opens
[[TeamManagementModal]], whose left column is the create-team form).

## Design

Mirrors [[AgentsHeaderMenu]]'s inline-panel approach (no Radix portal) so it
renders correctly inside the sidebar scroll container. Pure menu — both items
are thunks passed in by AgentList.
