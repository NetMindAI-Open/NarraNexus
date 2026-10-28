---
code_file: frontend/src/hooks/useAgentActions.ts
last_verified: 2026-09-11
stub: false
---

# useAgentActions.ts — shared rename / delete for one agent

## Why it exists

Two doors act on an agent: the agent profile page and the sidebar agent row's
⋯ menu (Owner-required, reinstated 2026-09-11). The last time rename/delete
lived in two copies (AgentList + EditAgentDialog vs. the profile page) they
drifted; one hook keeps the confirm dialog, the post-delete store cleanup and
the "renamed, with something to know" report identical.

## Contract

- The caller owns the dialog host: it passes `confirm` / `alert` from its own
  `useConfirm()` and renders that hook's `dialog` (no second dialog per page).
- `renameAgent(id, name)` — trims; blank → no call. `api.updateAgent(id, name)`
  (name only; description untouched), failure → alert, success → `refreshAgents()`
  (server truth; `agents` is persisted so a hand-patched row would resurface)
  then `warnAboutUpdateSideEffects` (`name_clash_with`,
  `identity_record_updated === false`). Resolves whether it landed.
- `deleteAgent(id, name)` — confirm (danger) → `api.deleteAgent` → failure
  alert, or: `refreshAgents()` → `clearAgent(id)` → if `id` was the active
  agent, re-point to the first remaining agent (or '') in both configStore and
  chatStore. Resolves `{ deleted, wasActive }`; navigation is the caller's call
  (both callers land on `/app/dashboard` when the agent on screen was deleted).
- Store access is through the hooks (`useConfigStore()` / `useChatStore()`),
  not `getState()`, so tests that mock `@/stores` with plain hooks keep working.
