---
code_file: frontend/src/components/nm/feedback.tsx
last_verified: 2026-05-18
stub: false
---

# nm/feedback.tsx — Skeleton, Spinner, ProgressBar

## Why it exists

Loading + progress primitives. Implements NM Axiom #8 (paper motion) via
the existing `.skeleton` class (shimmer keyframe from index.css) and
the `.animate-spin` Tailwind utility.

- `Skeleton` — placeholder shapes: rect (default), text (multi-line with
  60% last-line tapering), circle. Uses the global `.skeleton` class so
  the shimmer matches Tailwind's `animate-shimmer` rhythm.
- `Spinner` — single 1.5px ring with one transparent segment, rotating.
  Default ink color or species color (carbon/silicon/overlap).
- `ProgressBar` — thin ink rail + ink (or species) fill with smooth
  transition. Optional label + percentage.

## Upstream / Downstream

- **Upstream**: NM CSS tokens + `.skeleton` class from index.css.
- **Downstream**:
  - List loading states (Inbox, ManageAgents) — Skeleton text variant
  - In-button loading — `Button` uses an inline spinner with same visual
  - Async forms — Spinner during submit
  - BundleImport upload (M4) — ProgressBar with percentage
  - Bulk operation feedback — ProgressBar

## Design decisions

**Skeleton text variant tapers last line to 60%.** Reads as "partial
paragraph"; users immediately recognize the pattern as text-placeholder
rather than wondering "why is this row half-empty".

**Spinner uses one ring with transparent segment**, not multiple arcs.
Lighter, cheaper, and matches the "single bracket / single ring" NM
restraint vocabulary.

**ProgressBar rail at 10% ink alpha, fill at 100%.** Strong contrast
between empty and filled regions so glance-readability at small heights
(4px default) still works.

**ProgressBar clamps 0-100.** Defensive — guards against percentage
calculation errors elsewhere from breaking the rendering.

## Gotchas

- Skeleton text width defaults: all lines 100% except last = 60%. If the
  caller passes explicit `width`, it overrides for ALL lines including
  the last (no auto-taper).
- Spinner respects `prefers-reduced-motion` because of the global
  `animation-duration: 0.01ms !important` rule in index.css — under
  reduced motion the spinner appears static (acceptable: at 0.01ms it
  effectively shows one frame).
- ProgressBar transition is `width 300ms` — for rapidly-updating values
  (file upload), the smooth transition may visually lag actual progress.
  For high-precision real-time, disable via `style={{transition:'none'}}`.

## Related

- `nm/button.tsx` Button — inline spinner shares visual language
