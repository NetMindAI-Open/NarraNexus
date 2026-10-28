---
code_file: frontend/src/components/jobs/JobStatusMeter.tsx
last_verified: 2026-08-27
---

# JobStatusMeter.tsx — One-line health readout: bar + inline legend

Replaces `StatusDistributionBar.tsx` (deleted 2026-08-27) **and** the
`ui/StatStrip` call that sat above it.

## Why it exists

The panel used to open with two bands describing the same data twice: a
four-tile stat strip (Active / Success / Failed / Rate, ~116px) and a
distribution bar with its own title row (~60px). On a fresh agent that was
176px showing four zeroes and one flat grey bar.

This is the same information in ~34px, and it renders **only when there is a
distribution worth drawing** — `shouldShowMeter` in [[jobsPanelModel]]:
`total ≥ 4 || any failure`. Below that the whole band is absent.

## Design decisions

**Flow-laid legend, not a fixed four-column grid.** The grid is what produced
the truncated `SUCCES…` label at 400px drawer width: four equal columns each
had to fit an uppercase mono label. A wrapping flex row of only the non-zero
statuses cannot truncate.

**Zero-count statuses emit no legend entry at all.** Same rule as the filter
chips — see [[JobsPanel.tsx]] band C.

**The rate is hidden, not zeroed, before anything finishes.** `successRate`
returns `null` rather than `0`; a flat `0%` on a new agent reads as
"everything failed".

**3px bar.** A rule with weight, not a chart. Segment colors come from
[[jobStatusVisuals]] so the bar and the row dots can never disagree.

## Gotchas

- `data-nm="job-status-meter"` is the hook the density tests use to assert the
  band's presence/absence. It has no styling role; don't remove it as dead
  markup.
- The component reads `allJobs` (the unfiltered list), not the filtered one —
  the meter describes the whole picture regardless of which chip is selected.

## Upstream / downstream

- **Upstream:** [[jobsPanelModel]] (`shouldShowMeter`, `meterSegments`,
  `successRate`), [[jobStatusVisuals]]
- **Used by:** [[JobsPanel.tsx]] only
