---
code_file: frontend/src/components/teams/TeamManagementModal.tsx
last_verified: 2026-07-22
stub: false
---

# TeamManagementModal.tsx — Full team CRUD modal (create / manage the teams behind the group chats)

## Why it exists

The management surface behind the sidebar's TEAMS section: where the owner
creates teams, sets name/color/intro_md, and adds/removes member agents. Each
team it manages is the roster behind a group chat over the message bus, so
membership edits here directly change who participates in (and is `@mention`-able
within) that team's chat.

## How it works / design

- **Two-column layout**: left is the team list + a create form (name + color
  preset + Create); right is the selected team's metadata (name / color /
  intro_md) plus a member checklist and a Delete button. State is driven entirely
  by [[teamsStore]] (`createTeam` / `updateTeam` / `deleteTeam` / `addMember` /
  `removeMember`); the agent roster comes from `useConfigStore`.
- **Portals to `<body>`** via `createPortal`. The sidebar `<aside>` uses
  `translate` (mobile-drawer slide) which — even at the desktop value of 0px —
  establishes a containing block for `position:fixed` descendants, which would
  trap this overlay inside the 288px sidebar. Rendering into `<body>` escapes
  that subtree so the backdrop+modal are viewport-relative and centered.
- **All member toggles surface failures.** `handleToggleMember` wraps
  add/remove in try/catch and `window.alert`s any backend rejection. Before this,
  the handler leaned on unhandled-rejection propagation, so a 403 (cross-user
  agent / ownership mismatch) or 500 (schema drift / FK violation) silently did
  nothing — the user saw "click Add, nothing happens". Same alert-on-throw
  pattern guards create / save-meta / delete.
- **Gotchas**: `intro_md` edits land directly in `teams.intro_md` and are reused
  as the bundle's default README on export. Imported teams (`source === 'bundle'`)
  get an "imported" badge. Deleting a team only unlinks members — the agents
  themselves are not deleted (the confirm copy says so).

## 2026-07-21 — default-responder picker

Added a "Default responder" `<select>` (Auto = earliest member, or pick a current member) that
saves `lead_agent_id` via `updateTeam`. Backs the no-@mention routing in backend [[teams]].
`""` clears back to Auto. New i18n keys `teams.defaultResponderLabel|Auto|Hint`.

## 2026-07-22 — clear team data lives in the sidebar ⋮ menu (not here)

"Clear data" is intentionally NOT in this modal — to mirror agents (whose clear-data is only
in the row ⋮ menu), it lives in the team row's [[TeamRowMenu]] → [[AgentList]] renders
[[ClearTeamDataDialog]]. This modal keeps only rename/color/intro/members/default-responder
+ delete.
