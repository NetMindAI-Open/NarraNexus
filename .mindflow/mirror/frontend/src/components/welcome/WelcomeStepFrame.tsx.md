---
code_file: frontend/src/components/welcome/WelcomeStepFrame.tsx
last_verified: 2026-08-27
stub: false
---

# welcome/WelcomeStepFrame.tsx — the shell every welcome step is poured into

## Why it exists

So the three steps cannot disagree about what a flow must keep identical:
heading scale, column width, where the primary action lives, and the fact that
there is always a skip. A step that moved its CTA would read as a different
product.

## Design decisions

- **Body is vertically centred while the step fits**, top-aligned once it
  overflows (`my-auto` in a flex column; `justify-center` alone would clip the
  scroll). A short step pinned to the top of a tall pane looks unfinished —
  Owner 2026-08-27.
- Footer is pinned, body scrolls: the primary action stays reachable no matter
  how long the step's content is (the import list can be 29 rows).
- `onPrimary` is optional — the model step's CTA belongs to
  [[OneKeyOnboard]] itself, and two primaries on one screen is a bug.
- The heading is a `div role="heading" aria-level={1}`, not an `<h1>`:
  index.css styles bare `h1`-`h6` outside any cascade layer, so the unlayered
  rule beats Tailwind's utility — the same trap that rendered every dialog title
  at 32px (see [[Dialog]]).
