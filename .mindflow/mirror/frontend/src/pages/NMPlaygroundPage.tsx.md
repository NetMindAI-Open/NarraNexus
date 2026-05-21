---
code_file: frontend/src/pages/NMPlaygroundPage.tsx
last_verified: 2026-05-18
stub: false
---

# NMPlaygroundPage.tsx — Visual playground for NM primitives

## Why it exists

Internal dev-mode visual gallery showing every NM primitive in light + dark
side-by-side. Built per spec §7.1 to enable manual visual review of the
design system without spinning up a separate Storybook.

The page imports every export from `@/components/nm/index.ts` plus the
Radix tooltip/popover wrappers, and lays them out by category in `ThemePane`
duplicates (light wrapper + dark wrapper of the same content).

## Mount path

Not currently wired into the route table — load by direct import in
development or temporarily add a `<Route path="/app/nm-playground" element={<NMPlaygroundPage />}>` in `App.tsx` when reviewing.

Should NOT be mounted in production builds. (For Phase 1.5 we could add a
build-time tree-shake guard, but YAGNI for now — not linking from any nav
is sufficient.)

## Layout

- Header: BracketMarkLogo + page title + meta
- 12 sections, one per NM category (identity / bracket / surface / bubble /
  button / status / feedback / form / nav / modal / viz / misc)
- Each section = 2-column grid: light pane on left, dark pane on right.
  Dark pane has class `.dark` on a wrapper div so it picks up `.dark`-scoped
  tokens.

## Design decisions

**Side-by-side instead of theme toggle.** Reviewing color contrast issues
in dark mode requires seeing both at the same time — toggling is too slow
when comparing 50+ primitives. Side-by-side panes solve this for free.

**Inline state for controlled primitives.** Toggle/Checkbox/Radio/Slider/
TextInput/etc. all use local useState — playground is read-only, not
persisted anywhere.

**No dev-only guards.** The file is plain TSX; routes/wiring decides
visibility. Simpler than gating with import.meta.env.DEV — the page is
harmless if it leaks to production (just a long demo page).

## Gotchas

- Inline `<svg>` icons used directly in some samples to avoid pulling in
  lucide-react just for the playground (keeps the bundle off the critical
  path even if accidentally bundled).
- The bottom-nav demo uses emoji icons (💬 👥 👤) — quick demo placeholders;
  real BottomNavBar consumers should pass lucide icons.
- Some samples include FullChartCard with a non-chart placeholder
  (`ECharts canvas mounts here`) — actual ECharts mount happens in M4 when
  ChartCard is used in the Dashboard.

## Related

- `components/nm/index.ts` — barrel export this page exercises
- `lib/echarts-nm-theme.ts` — theme that ChartCard will use in production
- Spec: `reference/self_notebook/specs/2026-05-18-nm-design-system-design.md` §7.1
