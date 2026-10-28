---
code_file: frontend/src/components/jobs/JobRow.tsx
last_verified: 2026-08-27
---

# JobRow.tsx — One job in the list view, two lines, no card chrome

Extracted from `JobsPanel` in the 2026-08-27 density rebuild.

## Why it exists

The old row was a 76px bordered card that expressed the same fact three times —
a 32px status icon tile, a bordered status `Badge`, and a `line-clamp-1`
description. The description in particular was the worst offender: at drawer
width it truncated to a fragment ("Once a day, drop by with a fresh topic.
Pause or…") that carried no information at all.

The row is now ~52px: a 7px semantic dot, the title, the status word as plain
mono, and a second line carrying what users actually open this panel to read —
the schedule and the next/last run, from
[[jobsPanelModel]]`.describeRow`.

## Design decisions

**Hairline separation, not per-row cards.** Rows are divided by
`border-t border-[var(--rule)]` instead of each being a bordered, radiused
box. This also retires the nested-radius problem (design_system §3.2): there
is no inner box left to give a radius to.

**Dot, not a filled icon.** design_system §5 keeps the icon library
linear-only and expresses "solid" state as a semantic-colored geometric shape.
`pending` / `blocked` render as a hollow ring — nothing has happened yet.
Colors come from [[jobStatusVisuals]], the single status→visual table.

**Only noteworthy statuses tint their label.** If every status word were
colored, none of them would mean anything in a 30-row list. Attention statuses
additionally get a 2px `--color-error` left rail.

**The description moved to [[JobExpandedDetail.tsx]].** It opens the expanded
panel, where it has room to be a whole sentence.

## Gotchas

- Relative labels are computed at render inside `useMemo`, **not** on a timer.
  A running job's "elapsed 2m 04s" advances on the next refresh. A per-row
  interval would re-render the whole list once a second for a cosmetic digit —
  and 铁律 #14/#16 make "the agent has been running for hours" a normal case,
  so a long list of long-running jobs is exactly when that would hurt.
- The row is a `div role="button"`, not a `<button>`, because
  `JobExpandedDetail` renders interactive controls inside it and a button
  cannot nest buttons. Enter/Space are wired by hand as a result.

## Upstream / downstream

- **Upstream:** [[jobsPanelModel]] (`describeRow`, `formatRelative`,
  `isAttentionStatus`), [[jobStatusVisuals]]
- **Downstream:** renders its `children` (the expanded detail) when expanded
- **Used by:** [[JobsPanel.tsx]] list view only
