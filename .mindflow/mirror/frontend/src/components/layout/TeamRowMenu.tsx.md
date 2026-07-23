---
code_file: frontend/src/components/layout/TeamRowMenu.tsx
last_verified: 2026-07-22
stub: false
---

> 2026-06-24: added an **Add agent** item (UserPlus, above Rename). This
> re-homes dev's #43 "create an agent already in this team" capability — the old
> hover-`+` lived on the [[AgentGroupSection]] team header, which no longer
> exists now that teams render as single [[TeamChatRow]]s. `onAddAgent` +
> `addingAgent` (disables the item / shows "Adding…" mid-create) come down from
> [[AgentList]] (`handleCreateAgentInTeam` → `createAgent({ teamId })`).
> `MenuItem` gained a `disabled` prop for this.

# layout/TeamRowMenu.tsx — Kebab (⋮) menu for the team group-chat row

## Why it exists

Mirrors [[AgentRowMenu]] so a team row ([[TeamChatRow]]) offers the same
Add agent / Rename / Delete affordances as an agent row. Inline absolute panel
(no portal) so it works inside the sidebar scroll container.

## Gotcha

`onOpenChange` is fired from the click handler (`setOpenAndNotify`), NOT from
inside a `setState` updater — calling the parent's setter during render
triggers React's "cannot update a component while rendering a different
component" warning. (AgentRowMenu had this latent bug; both are now fixed the
same way.)

## 2026-07-22 — Clear data item

Added an `onClearData` prop + "Clear data" MenuItem (Eraser icon, between Rename and Delete),
mirroring the agent row's clear-data affordance. Opens [[ClearTeamDataDialog]] via
[[TeamChatRow]] → [[AgentList]]. i18n `layout.teamRowMenu.clearData`.
