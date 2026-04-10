---
code_file: frontend/src/components/steps/StepsPanel.tsx
last_verified: 2026-04-10
---

# StepsPanel.tsx — Standalone execution steps panel (possibly vestigial)

Minimal wrapper: reads `currentSteps` from `useChatStore`, filters to the six
main step IDs (`'0'`–`'5'`), shows a progress badge, and renders `StepCard`
for each.

This was the original standalone execution view before the steps were
integrated into `RuntimePanel`'s execution tab. `RuntimePanel` now re-uses
`StepCard` directly and provides a richer dashboard (KPI cards, progress
ring). `StepsPanel` is not known to be rendered anywhere in the current
layout — verify before modifying.
