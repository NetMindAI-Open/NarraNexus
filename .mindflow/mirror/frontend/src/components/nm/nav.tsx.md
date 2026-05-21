---
code_file: frontend/src/components/nm/nav.tsx
last_verified: 2026-05-18
stub: false
---

# nm/nav.tsx — Navigation primitives (TabBar, SidebarNavItem, Breadcrumb, StepIndicator, BottomNavBar)

## Why it exists

5 navigation primitives. Implements Axiom #1 (no species in chrome — all
nav uses ink, never Carbon/Silicon) + Axiom #7 (mono labels for nav).

- `TabBar`: underline-only tabs (NOT pills); ink underline (NOT carbon);
  optional count badge per tab
- `SidebarNavItem`: 2px ink left-edge + paper-warm hover; active=bold +
  aria-current=page
- `Breadcrumb`: mono `/` separator; last item = aria-current; clickable
  items via href OR onClick
- `StepIndicator`: ordered list of `[N]` numbered markers + connecting
  lines; current = filled ink; done = ink-50; pending = ink-30
- `BottomNavBar`: mobile-style 5-tab nav with icon + mono label below

## Upstream / Downstream

- **Upstream**: NM CSS vars
- **Downstream**:
  - `layout/Sidebar` (M3) — wraps SidebarNavItem rows for routing
  - `chat/ChatPanel` filter bar — TabBar for all/human/ai filter
  - `pages/SetupPage` (M4) — StepIndicator for wizard steps
  - Mobile responsive Phase 1.5 — BottomNavBar replaces Sidebar at <768px
  - Pages with nested routes — Breadcrumb in header

## Design decisions

**TabBar underline is ink, not carbon.** Tabs are navigation (chrome), not
species identity (per Axiom #1). Using Carbon for the active tab would
imply "this is a human action zone" — incorrect.

**SidebarNavItem active uses LEFT-edge 2px ink stripe.** The existing
`.agent-item.active::before` pattern from Archive — preserved here as a
component-encapsulated version. Reads as "this is the current location"
without competing with the species-colored content inside.

**Breadcrumb last item gets ink color + aria-current=page.** The visual
weight signals "you're here"; the aria attribute signals it to SR.

**StepIndicator uses `[N]` numbered markers, not dots.** Per NM Axiom #6
(bracket motif). The `border-radius: var(--radius-xs)` (4px) gives the
markers a faint "bracket" character rather than full circle.

**BottomNavBar icons + 10px mono labels.** Optimized for mobile thumb
zone; labels uppercase mono ensures they read at small size.

## Gotchas

- TabBar's active underline uses CSS transition `width: 0 → 100%` — this
  creates a "growing underline" effect when switching tabs. Looks good but
  if multiple tabs switch in quick succession, last write wins (no queue).
- SidebarNavItem's active background `bg-paper-warm` and hover are the
  same color — intentional, makes the transition non-jumpy when activating.
- StepIndicator's connecting line color reflects state: ink-50 if the
  *next* step is done, ink-30 otherwise. Reads as "completed path so far".
- Breadcrumb's mono `/` separator uses `opacity-50` so it visually
  retreats — items lead.

## Related

- `nm/bracket.tsx` BracketSectionLabel — sibling for sidebar section headers
- Spec: `reference/self_notebook/specs/2026-05-18-nm-design-system-design.md` §5.7
