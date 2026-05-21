---
code_file: frontend/src/components/nm/viz.tsx
last_verified: 2026-05-18
stub: false
---

# nm/viz.tsx — KPITile, StatStrip, ChartCard

## Why it exists

Wrapper primitives that wire NM tokens to data display surfaces. Uses
PaperCard + BracketSectionLabel from sibling primitives so the data viz
inherits the design language without re-implementing surfaces.

- `KPITile`: PaperCard with mono uppercase label + display number + optional
  trend arrow with `upIsGood` toggle (Cost going UP is bad, etc.)
- `StatStrip`: horizontal strip of KPI-like cells, hairline-divided
- `ChartCard`: PaperCard + BracketSectionLabel header + actions slot + min-height chart canvas region

## Upstream / Downstream

- **Upstream**: `nm/surface.tsx` PaperCard, `nm/bracket.tsx` BracketSectionLabel
- **Downstream**:
  - Dashboard (M4) — 4 KPITiles + ChartCard hosting nm-light ECharts
  - SystemPage (M4) — StatStrip for service health summary
  - CostPanel (M4) — ChartCard with species-colored series

## Design decisions

**KPITile trend semantics decoupled from arrow direction.** `upIsGood`
prop lets the same `↑ 12.3%` arrow be green for "messages up" and red for
"cost up". Without this, a caller would have to invert the trend sign
just to color it — confusing.

**Number uses display font + tabular-nums.** Display font carries the
"this is a moment of magnitude" feel. Tabular nums prevent the digit
widths from changing when the number updates (avoids layout jitter on
live dashboards).

**ChartCard min-height default 240px.** Standard for the dashboard
activity chart. Callers can override (Cost panel uses larger).

**StatStrip uses Tailwind `divide-x` (with custom border color).**
Single-color hairline divider for the inline data row pattern.

**ChartCard does NOT include the chart library** — it's a wrapper only.
Callers pass ECharts/ReactFlow/etc. as children. Keeps this primitive
viz-library-agnostic.

## Gotchas

- KPITile arrow is rendered as text (`↑` `↓` `·`) for accessibility (SR
  reads "up" or "down arrow") and to avoid SVG bundle cost for tiny icons.
- `trend === 0` is treated as "flat" (gray dot). Negative `trend` infers
  `down` direction. To force a direction independent of sign, set
  `trendDir` explicitly.
- `value` accepts ReactNode so callers can pass a formatted string or
  even a small SVG sparkline.
- ChartCard's BracketSectionLabel renders title in uppercase — caller
  shouldn't pre-uppercase.

## Related

- `lib/echarts-nm-theme.ts` — ECharts theme registered at app boot
- `lib/reactflow-nm-config.ts` — ReactFlow defaults
- Spec: `reference/self_notebook/specs/2026-05-18-nm-design-system-design.md` §5.11
