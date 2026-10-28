---
code_file: frontend/src/components/welcome/WelcomeRail.tsx
last_verified: 2026-08-27
stub: false
---

# welcome/WelcomeRail.tsx — the first-run flow's left rail

## Why it exists

A first-run flow that hides its own length reads as endless. The rail shows the
step list ([[welcomeSteps]] — never a step the flow won't render), plus the two
escapes a first screen must always offer: language and log out.

## Design decisions

- **Graph paper, rail only.** 72px cells at 4% ink (`--nm-welcome-grid`) — the
  spec lifted verbatim from narra.nexus's `body:before` overlay, re-expressed in
  warm ink to match this app's paper. The content pane stays a plain L1 card so
  forms read cleanly (Owner decision 2026-08-27). Dark uses a higher alpha for
  equal perceived weight, same reason as the row-hover pair (design_system §2.5).
- **Logo lockup is deliberately larger than the sidebar's** (h-11): on a first
  run this rail is the only place the product introduces itself. The mono
  `[ first run ]` label that sat under it was cut (Owner 2026-08-27) — with the
  step list right below, it was a third competing label in one column.
- **Below md the rail becomes a top progress strip** with the same data — the
  DMG's smallest window must be able to finish the flow (binding rule #7).
- `logout` here does NOT confirm first (see [[sessionWipe]]): a user who just
  arrived has nothing to lose, and a confirm dialog on screen one is friction.
