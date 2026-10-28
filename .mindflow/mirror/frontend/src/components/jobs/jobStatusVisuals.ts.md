---
code_file: frontend/src/components/jobs/jobStatusVisuals.ts
last_verified: 2026-09-10
---

## 2026-09-10 — `paused_spend_cap`

新增行：warning 色、实心点、`jobs.status.pausedSpendCap`。与 `paused_no_quota` 同视觉——
两者都是「平台替你停了、需要你知道」的暂停；区别只在文案。`Record<JobStatus, …>` 的穷尽
约束是这张表唯一的登记门禁：后端加状态而这里没加，tsc 直接红。

# jobStatusVisuals.ts — The single status → color/label table

## Why it exists

Before the 2026-08-27 density rebuild there were two of these and nothing
forcing them to agree: `statusConfig` inline in `JobsPanel` (icon + text class
+ bg class, 11 entries) and a hand-written chain of segment colors inside
`StatusDistributionBar`. A status added to one and not the other would render
inconsistently in two places on the same screen.

Now one `Record<JobStatus, StatusVisual>`, consumed by the meter segments, the
meter legend dots, the row dots, and the row status words.

## Design decisions

**Colors are semantic tokens only** (design_system §2): no palette primitives,
no hex. `--color-error` / `--color-warning` / `--color-success`, plus
`--text-tertiary` for the statuses that are normal rather than noteworthy.

**Status is geometry, not a filled icon.** §5 keeps the icon library
linear-only; solid state is expressed as a colored dot. `hollow: true`
(pending / blocked) draws a ring instead — nothing has run yet.

**`active` is ink, not a semantic color.** It means "the Module instance is
alive but no script is executing", which is normal. It previously used
`--accent-primary`, which resolves to `--nm-ink` anyway; naming it directly
removes the implication that there is an accent hue involved.

**`shouldTintStatusLabel` is separate from the dot color.** Every status has a
dot; only the noteworthy ones tint their *word*. If all 11 words were colored,
the two that matter would not stand out in a 30-row list.

## Gotchas

- The record is exhaustive over `JobStatus` by type. Adding a status to
  `types/api.ts` without adding it here is a compile error — that is
  deliberate, and the reason this is a `Record` rather than a lookup with a
  default.
- `statusVisual()` still falls back to `pending` at runtime, for API responses
  carrying a status the frontend build does not know about yet.

## Upstream / downstream

- **Upstream:** `types/api.ts` (`JobStatus`), i18n `jobs.status.*`
- **Used by:** [[JobsPanel.tsx]] (chip labels), [[JobStatusMeter.tsx]],
  [[JobRow.tsx]]
