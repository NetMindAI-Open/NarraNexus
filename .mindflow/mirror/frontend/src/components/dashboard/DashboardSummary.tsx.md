---
code_file: frontend/src/components/dashboard/DashboardSummary.tsx
last_verified: 2026-06-24
stub: false
---

# DashboardSummary.tsx — top-of-page status strip that doubles as the colour legend

## Why it exists

The dashboard has a left-rail colour per agent health, but a colour means
nothing until you've learned it. This strip teaches the legend by being the
legend: it shows the total agent count plus one chip per non-empty health
bucket, and each chip's dot is the same colour as that state's rail. Reading it
once is the whole tutorial — there is no separate "Legend" affordance to hover.

## How it works / design

- Consumes `agents: AgentStatus[]` from the dashboard page; it does not poll —
  it re-renders when the parent's polled `agents` state changes.
- Bucketing: counts by `health` for owned agents, but folds every
  public/non-owned agent into `healthy_idle`, because `PublicAgentStatus` has
  no `health` field by permission design (see [[api.ts]] / [[_dashboard_helpers]]).
  Returns `null` at zero agents — the empty-state component handles that case.
- `CHIP_ORDER` is attention-first (error → ack → warning → paused → running →
  idle → quiet) so what needs action surfaces left; "needs attention" header
  only renders when error+warning+paused > 0. Zero-count buckets are skipped.
- Design decisions / gotchas: this v2.3 pass moved the total to a bold "N
  agents" anchor and put every chip's label inline, which let the old "hover
  for meaning" hint be deleted (labels are always visible). The chip colours
  here are hard-coded via CSS vars (`dotCls`/`textCls`), independent of
  [[healthColors]]'s `HEALTH_COLORS` map — the known duplication tax. As part
  of the "silicon" polish, the idle chip dot is the brand
  `--color-silicon` (not Tailwind sky) and the idle label uses neutral
  `--text-primary` ink, matching the idle rail in [[healthColors]] so idle no
  longer reads as "blue". A new `AgentHealth` value must be added to both
  `counts` and `CHIP_ORDER` or it silently won't be tallied.
