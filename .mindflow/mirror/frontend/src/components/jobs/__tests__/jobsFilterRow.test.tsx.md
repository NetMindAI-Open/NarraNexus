---
code_file: frontend/src/components/jobs/__tests__/jobsFilterRow.test.tsx
last_verified: 2026-08-27
stub: false
---

# jobsFilterRow.test.tsx — the panel's chrome must stay proportional to its data

## 为什么存在

Two incidents, same surface.

**2026-07-30** — the 11 status chips in [[JobsPanel]] *rendered fine*
(`getByRole('button')` found all of them) but four were clipped out of view by
a `ScrollArea horizontal hideScrollbar` in a 300–440px drawer, with no
scrollbar and no edge fade. Owner: "任务列表里筛选没显示全，只显示了 6-7 个".
The lesson recorded then still holds: **"the chip is in the DOM" is not the
property worth testing.** jsdom has no layout, so this file asserts the
*structural* properties that make the failure impossible.

**2026-08-27** — the density rebuild removed the cause rather than the symptom:
the chip row is now derived from the data, so a status with no jobs has no
chip. That inverted the old assertion. "Every one of the 11 chips renders" is
now itself the bug being guarded against, because 7–9 of those chips could only
ever produce an empty list.

## 现在断言什么

1. chips exist for exactly the statuses that have jobs, plus `All`;
2. every chip carries its count, `All` carries the total;
3. zero jobs → **no filter row at all**;
4. the row still carries `flex-wrap` and is still outside
   `[data-radix-scroll-area-viewport]` (the 2026-07-30 guard, kept as a safety
   net now that the row is short);
5. attention statuses lead the row, so failures are one glance from `All`;
6. the meter band renders only at `total ≥ 4 || any failure`;
7. a collapsed row shows schedule + next run, **not** the description.

## 上下游关系

- **测的是**: [[JobsPanel]]'s chrome — filter row, meter band, and row content.
  Rendered with `embedded` (the bookmark-drawer form, where both bugs bit).
- **依赖谁**: `test-setup.ts` initialises i18next, so assertions match real
  English labels rather than raw keys. `@/stores` is mocked with a
  module-level mutable `JOBS` array, because every assertion here is about how
  the chrome responds to *different* data.
- **不测什么**: the rules themselves (thresholds, ordering, row phrasing) are
  unit-tested in [[jobsPanelModel]]'s own suite. This file tests that
  `JobsPanel` actually wires them up.

## 新人易踩的坑

- The old `CHIP_LABELS` constant is gone. Adding a status no longer means
  touching this file — it means adding it to `STATUS_ORDER` in
  [[jobsPanelModel]] (which is exhaustive over `JobStatus` by type) and to
  [[jobStatusVisuals]].
- The chips carry an explicit `aria-label` (`"All 3"`). Without it the label
  and the count are adjacent inline spans and the computed accessible name is
  `"All3"` — which is both untestable by name and wrong for screen readers.
- The meter is located via `data-nm="job-status-meter"`. That attribute exists
  for this test; it has no styling role, so don't sweep it as dead markup.
