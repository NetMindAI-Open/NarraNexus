---
code_file: frontend/src/components/welcome/StepImport.tsx
last_verified: 2026-08-27
stub: false
---

# welcome/StepImport.tsx — welcome step 2, bring existing agents over

## Why it exists

Renders the same [[ImportAgentPicker]] the sidebar modal renders, on the same
[[useAgentImport]] state, with a flow footer instead of dialog buttons. The step
only exists when detect actually found something ([[welcomeSteps]] drops it
otherwise), so there is no empty state to design here.

## Design decisions

- **Advances on the batch report, not on the last apply.** The user should get to
  read what landed — and retry a failed row — before the flow moves on.
- While the queue runs, the skip slot becomes "stop after this one": the only
  safe interruption is between rows, never mid-write (see
  [[migrationImportQueue]]).
- Declining the step arms the sidebar "+" coach-mark from [[WelcomePage]], so a
  user who says "not now" still learns where import lives.
