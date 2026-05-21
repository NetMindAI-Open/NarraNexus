---
code_file: frontend/src/lib/echarts-nm-theme.ts
last_verified: 2026-05-18
stub: false
---

# echarts-nm-theme.ts — ECharts theme bridge for NM design system

## Why it exists

ECharts ships with two built-in themes (`dark`, default light) that look like
scientific plotting software — cold gray grid, blue/red default series. Neither
fits the NM design system, which is warm paper + warm ink + species colors.

This module registers two ECharts themes (`nm-light`, `nm-dark`) that mirror
the NM `index.css` tokens: warm ink axes, hairline grid, paper-raised tooltip,
SF Pro font stack with CJK fallback. Default series colors are an ink ramp
(5 tints from full ink to ink-30); the species palette (Carbon / Silicon /
Overlap) is exported separately for explicit opt-in when a chart needs to
distinguish human-vs-AI data.

## Upstream / Downstream

- **Upstream**: `echarts` package's `registerTheme()` API. Module side-effect
  registers on import — consumers can do `import './lib/echarts-nm-theme'`
  once at app boot, or call `registerNMEChartsTheme()` explicitly.
- **Downstream**: any component using `echarts.init(dom, theme)`. The exported
  helper `pickNMTheme()` returns the right theme name based on
  `document.documentElement.classList.contains('dark')`.
- **Loaded from**: `main.tsx` (side-effect import added in M1) → ensures themes
  are registered before any chart instantiates.

## Design decisions

**Auto-register on import.** Side-effect at module bottom. Trade-off: less
explicit lifecycle. Benefit: consumers don't have to remember to call init —
matches React 19 "code that wants to run once at startup" idiom.

**Tooltip background = paper-raised (`#F5F2EB`).** Tooltip needs to "lift"
above the chart surface (Axiom #4). Using paper-raised instead of a shadowed
white card keeps the warm paper feel and avoids breaking the no-shadow rule.

**Default series = ink ramp, not species colors.** Most NarraNexus charts
(dashboard activity, KPI trend) don't show "human vs AI" — they show
aggregate metrics. Using Carbon orange as the default series would steal the
species color for non-species purposes (Axiom #1 violation). Species colors
live in the exported `species` object for explicit opt-in.

**5 ink tints, not 8.** ECharts uses up to 9 colors by default. NM is
restraint-first — 5 series readable without legend lookup is more useful than
9 with rainbow tail.

## Gotchas

- ECharts `registerTheme` is module-global and stores by name. Re-registering
  same name overrides silently. Tests reset by re-calling — no cleanup needed.
- `splitArea.areaStyle.color: ['rgba(...02)', 'transparent']` creates faint
  alternating band shading on grid — barely visible on warm paper but adds
  readability for dense series.
- `extraCssText` is the only ECharts way to set tooltip border-radius — the
  `borderRadius` property doesn't work for tooltips.
- jsdom doesn't actually render charts; the test mocks `echarts` to avoid
  pulling in the 700KB runtime.

## Related

- `lib/reactflow-nm-config.ts` — sibling NM bridge for ReactFlow
- `lib/__tests__/echarts-nm-theme.test.ts` — unit test
- Spec: `reference/self_notebook/specs/2026-05-18-nm-design-system-design.md` §6.2
