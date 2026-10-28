---
code_file: frontend/src/components/layout/AgentRowMenu.tsx
last_verified: 2026-09-11
stub: false
---

## 2026-09-11 — the dropdown shell moved to RowKebabMenu

The trigger, panel, item buttons, `useDismissOnOutside` wiring and the
`onOpenChange` notification now live in the shared [[RowKebabMenu]]; this
file is only the three items (Rename / Model & framework / Delete, panel
`min-w-[150px]`). It had been a line-for-line copy of [[TeamRowMenu]]'s shell;
one copy means a panel restyle cannot drift between the agent and team rows.
Behaviour unchanged.

## 2026-09-11 — reinstated (OWNER-REQUIRED entry): Rename / Model & framework / Delete

#383 (19aae3ada, 2026-09-04) deleted this file together with its mirror: every
agent action moved to the new agent profile page and the sidebar row became
display-only. The Owner's 2026-09-11 report ("the three dots behind each agent
in the left sidebar are gone — restore them: rename / delete / setting model")
overrules that. **This menu is an Owner-required entry; do not remove it as a
duplicate of the profile page.**

Re-applied onto the current code rather than restored verbatim:

- Items are exactly the Owner's three: **Rename** (starts the row's inline
  rename), **Model & framework** (opens the same AgentLlmConfigPanel as the
  chat-header button and the profile page), **Delete** (danger). Not restored
  from the pre-#383 menu: *Edit…* (EditAgentDialog is gone; the description is
  edited on the profile's Settings tab), *Clear data…* (lives on the profile
  page's kebab only), and the public/private toggle (the feature flag it hung
  off was removed with #383 and the public-agent feature is still paused).
- The menu is owner-only as a whole: the host ([[AgentGroupSection]]) renders it
  only when `agent.created_by === currentUserId` (every action is owner-only
  server-side; the old "rename is shown to everyone" rule is gone).
- Props are plain callbacks (`onRename` / `onOpenModelConfig` / `onDelete` /
  `onOpenChange`); the menu stops click propagation itself so an item never
  also selects the row.
- Kept from before: inline absolute panel (no portal — sidebar scroll
  container), `useDismissOnOutside` for outside-click / Escape, and
  `onOpenChange` so the host row can lift itself (`relative z-30`) above the
  next row's stacking context.

Pinned by `__tests__/agentGroupSection.test.tsx` (menu items, owner-only,
routing, inline rename) and `__tests__/agentListRowMenu.test.tsx` (host wiring).

## Lessons carried over from the pre-#383 menu

- 2026-06-11: every agent row is its own stacking context (`animate-slide-up`
  keeps a transform with fill-mode forwards), so the panel's `z-50` cannot rise
  above the NEXT row — the host must lift the row while open (`onOpenChange`).
- 2026-08-19: a full-screen backdrop `<div>` for outside-click dismissal is
  hijacked by that same transform ancestor (it only covers one row); use the
  document-level `useDismissOnOutside` instead (also gives Escape).
- Notify `onOpenChange` from the event handler, never inside a setState
  updater (cross-component setState-during-render warning).
