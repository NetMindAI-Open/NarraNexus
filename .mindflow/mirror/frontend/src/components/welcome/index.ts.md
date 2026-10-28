---
code_file: frontend/src/components/welcome/index.ts
last_verified: 2026-08-27
stub: false
---

# welcome/index.ts — barrel for the first-run flow

## Why it exists

[[WelcomePage]] is the only composer of these pieces; the barrel keeps its import
list short and marks the boundary — nothing outside the flow should reach into a
step component.

## Gotcha

- Deliberately exports no constants (the rail's grid style stays module-private):
  a component module that also exports values breaks fast refresh.
