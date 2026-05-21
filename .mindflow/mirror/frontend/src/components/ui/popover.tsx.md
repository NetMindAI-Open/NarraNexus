---
code_file: frontend/src/components/ui/popover.tsx
last_verified: 2026-05-18
stub: false
---

# popover.tsx — Radix Popover with NM design system styling

Thin wrapper over `@radix-ui/react-popover`. NM-styled in M2: RaisedPanel
treatment — paper-raised bg + hairline border + radius-md + soft lift
shadow (the second allowed shadow exception after RaisedPanel + Toast +
Dialog).

Slide-in direction matches the trigger side via Radix `data-side` attribute.

Used by `CostPopover` in the context panel header and other dropdown
patterns. Not re-exported from `index.ts` — import as
`@/components/ui/popover`.

## Why RaisedPanel-style

Per NM Axiom #4: a popover IS by definition "above the page". RaisedPanel
treatment makes it visually consistent with every other "above" surface
(Dropdown, Toast, Dialog).

## Related

- `nm/surface.tsx` RaisedPanel — same visual language for non-popover surfaces
- Spec: §5.9
