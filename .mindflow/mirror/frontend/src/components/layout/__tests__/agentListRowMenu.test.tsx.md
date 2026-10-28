---
code_file: frontend/src/components/layout/__tests__/agentListRowMenu.test.tsx
last_verified: 2026-09-11
stub: false
---

# agentListRowMenu.test.tsx

AgentList-level wiring of the Owner-required agent row ⋯ menu: Model &
framework opens the (stubbed) AgentLlmConfigPanel for that row and closes;
Rename goes through `api.updateAgent` and refreshes the list; Delete shows the
confirm dialog (Cancel → nothing deleted); confirming deletes through
`api.deleteAgent`, re-points configStore/chatStore to the remaining agent and
navigates to `/app/dashboard` when the deleted agent was active, and leaves the
view alone when another agent is deleted.
