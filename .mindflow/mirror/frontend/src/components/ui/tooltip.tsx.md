---
code_file: frontend/src/components/ui/tooltip.tsx
last_verified: 2026-05-18
stub: false
---

# tooltip.tsx — Radix Tooltip with NM design system styling

Thin wrapper over `@radix-ui/react-tooltip`. NM-styled in M2: inverted
ink-on-paper bubble + radius-sm + paper text + soft scale-in motion.
No shadow (the inversion itself carries the "lifted" visual). Requires
`TooltipProvider` to wrap the app or at least the consuming component tree.

## Why inverted

Per NM Axiom #4 (lift via paper not shadow): tooltips need to feel "above"
the content, but the content already lives on warm paper. Inverting to ink
fill is the most paper-respecting way to lift — it reads as "this is a
label peeking through from a deeper layer", not a "popping out" card.

## Related

- `nm/feedback.tsx` (no Tooltip wrapper there — this file is the canonical
  one; only Popover gets a separate Radix re-export name)
- Spec: §5.9
