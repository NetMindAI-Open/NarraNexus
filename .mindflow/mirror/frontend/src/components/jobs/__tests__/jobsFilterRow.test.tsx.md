---
code_file: frontend/src/components/jobs/__tests__/jobsFilterRow.test.tsx
last_verified: 2026-07-30
stub: false
---

# jobsFilterRow.test.tsx — the filter chips must all be reachable

## 为什么存在

Regression guard for a bug that was invisible to every existing test: the 11
status chips in [[JobsPanel]] *rendered fine* — `getByRole('button')` found all
of them — but four of them were clipped out of view by a
`ScrollArea horizontal hideScrollbar` in a 300–440px drawer, with no scrollbar
and no edge fade to hint they were there. The Owner reported it as "任务列表里
筛选没显示全，只显示了 6-7 个".

So "the chip is in the DOM" is NOT the property worth testing here. jsdom has
no layout, so this file asserts the *structural* property that makes clipping
impossible instead:

- every chip renders (the cheap half), and
- the row carries `flex-wrap` and is **not** inside a
  `[data-radix-scroll-area-viewport]` — i.e. overflow resolves by wrapping,
  never by scrolling something the user can't see.

## 上下游关系

- **测的是**: [[JobsPanel]]'s filter row only. Rendered with `embedded` (the
  bookmark-drawer form, which is where the bug bit); no store seeding needed
  because the filter row renders regardless of `allJobs.length`.
- **依赖谁**: `test-setup.ts` initialises i18next, so the assertions can match
  the real English labels rather than raw keys.

## 新人易踩的坑

Chip labels are duplicated in `CHIP_LABELS` here and in the status array in
[[JobsPanel]]. Adding a status means touching both — the duplication is
deliberate (a shared constant would let a rename pass the test vacuously), but
it is a duplication.
