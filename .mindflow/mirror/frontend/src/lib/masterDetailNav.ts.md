---
code_file: frontend/src/lib/masterDetailNav.ts
last_verified: 2026-09-10
stub: false
---

# masterDetailNav.ts — shared responsive master–detail nav classes

SettingsPage and DashboardPage render the same shape: a rail of section
buttons beside a detail pane. At `md`+ the rail is a fixed `md:w-56`
column; below `md` a fixed column ate most of a 360px viewport (GitHub
#130), so the wrapper stacks (`flex-col md:flex-row`) and the rail becomes
a horizontal `overflow-x-auto` strip whose items are `shrink-0`.

- `MASTER_DETAIL_ROW_CLASS`: wrapper around rail + detail pane.
- `MASTER_NAV_CLASS`: the `<nav>` rail.
- `MASTER_NAV_ITEM_CLASS` / `masterNavItemClass(isActive)`: one rail
  button; the function adds the active/inactive colours through `cn()`
  (tailwind-merge; none of the state classes conflict with the base, which
  `masterDetailNav.test.ts` asserts).

Why it exists: the "both rails look identical" invariant used to live
only in the two pages' mirrors while the class strings were copied
byte-for-byte; a breakpoint or width change in one page would drift
silently. Now there is one definition. The breakpoint pin lives in
`lib/__tests__/masterDetailNav.test.ts`; each page test asserts its rail
uses these exact strings. Deliberately constants, not a component: two
call sites do not justify a `<MasterDetailNav>` abstraction.
