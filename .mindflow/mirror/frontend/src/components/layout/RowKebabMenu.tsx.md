---
code_file: frontend/src/components/layout/RowKebabMenu.tsx
last_verified: 2026-09-11
stub: false
---

# layout/RowKebabMenu.tsx — shared kebab dropdown shell for sidebar rows

## Why it exists

[[AgentRowMenu]] (reinstated 2026-09-11) and [[TeamRowMenu]] had become
line-for-line copies of one dropdown: same trigger classes, same panel
classes, same `useDismissOnOutside` wiring, same `onOpenChange`
notification, each with a private `MenuItem`. Two copies of a design-system
surface drift the first time only one is restyled, so the shell lives here
and both menus are item lists (`{key, icon, label, danger?, disabled?,
onSelect}`).

## Contract the hosts rely on

- `onOpenChange(open)` fires from the event handler on open and on every
  close (item click, outside pointerdown, Escape). Hosts lift their row
  (`relative z-30`) with it: every sidebar row is its own stacking context
  (`animate-slide-up` keeps a transform), so the panel's `z-50` alone cannot
  rise above the next row.
- Every click inside stops propagation — a menu action never also selects
  the row.
- A `disabled` item is a disabled `<button>`: no `onSelect`, menu stays open.
- Inline absolute panel, no portal (works inside the sidebar scroll
  container); dismissal is document-level, never a full-screen backdrop (a
  backdrop inside a transformed row covers only that row).

Scope: only the two sidebar row menus. The agent profile page's header menu
(bordered h-9 trigger) and the chat/team header dropdowns are different
surfaces and are not built on this.

Pinned by `__tests__/rowKebabMenu.test.tsx`; the hosts by
`agentGroupSection.test.tsx` / `agentListRowMenu.test.tsx` /
`popoverDismiss.test.tsx`.
