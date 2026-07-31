---
code_file: frontend/src/components/layout/CreateMenu.tsx
last_verified: 2026-07-21
stub: false
---

# layout/CreateMenu.tsx — The "+" create dropdown (Agent / Team / Import)

## Why it exists

Surfaces teams as a first-class creatable object alongside agents (the
homepage's team-first model). Replaces the former single create-agent "+"
button in [[AgentList]]'s header with a dropdown: **Create Agent**
(the existing `useCreateAgent` flow), **Create Team** (opens
[[TeamManagementModal]], whose left column is the create-team form), and an
optional **Create Agent (from other source)** — the Agent Migration entry
point ([[ImportAgentModal]]).

## Design

Mirrors [[AgentsHeaderMenu]]'s inline-panel approach (no Radix portal) so it
renders correctly inside the sidebar scroll container. Pure menu — all items
are thunks passed in by AgentList. `onImportAgent` is **optional**: AgentList
only passes it in local/desktop mode, since the migration scanner reads the
filesystem and 503s on cloud. When absent, the import item is hidden.
