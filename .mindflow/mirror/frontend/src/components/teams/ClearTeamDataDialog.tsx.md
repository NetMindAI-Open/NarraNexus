---
code_file: frontend/src/components/teams/ClearTeamDataDialog.tsx
last_verified: 2026-07-22
stub: false
---

# ClearTeamDataDialog.tsx — clear a team's chat / shared files

## Why it exists

Team counterpart to `ClearAgentDataDialog`. A team is a collaboration surface (group-chat
history + `_shared/teams/{id}` files); the owner needs a way to wipe that without deleting
the team. Two checkboxes — chat / files (chat defaults on, files opt-in) — map to
`api.clearTeamData(teamId, {chat, files})` → `DELETE /api/teams/{id}/data`. Danger-styled
confirm, disabled until a scope is picked.

## Upstream / downstream

- Rendered by [[AgentList]] (opened from the team row's [[TeamRowMenu]] ⋮ → "Clear data",
  mirroring how the agent clear-data dialog is opened from the agent row menu). AgentList
  owns the open/busy state (`clearTeamTarget` / `clearTeamBusy`).
- Backend: [[teams]] `_wipe_team_data` — keeps team, members, bus channel; deletes only the
  room's `bus_messages` and/or the shared-files dir.
- i18n under `teams.clearData.*` (en+zh).
