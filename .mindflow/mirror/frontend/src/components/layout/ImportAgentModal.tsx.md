---
code_file: frontend/src/components/layout/ImportAgentModal.tsx
last_verified: 2026-08-27
stub: false
---

# layout/ImportAgentModal.tsx — dialog chrome around the import picker

## Why it exists

The sidebar "+" entry point for "import agents from other tools on this machine".
Since 2026-08-27 it is only the CHROME: the list, the inline row detail and the
batch report live in [[ImportAgentPicker]], its state in [[useAgentImport]],
because step 2 of the first-run flow ([[StepImport]]) shows the same picker as a
full page. What is left here is what a dialog actually owns — the title per
phase, the footer buttons, and what closing means.

Two rewrites in one day, both Owner decisions: the four-stage
framework → source → preview → done wizard became a one-page multi-select picker
(the wizard could only import ONE source per open, so 26 Claude Code projects
meant reopening the modal 26 times), and then the picker was extracted so the
welcome flow could host it.

## Design decisions

- **Local only.** detect/scan read the user's filesystem and 503 on cloud, so
  callers ([[Sidebar]]) mount this in local mode only.
- **X / Esc / backdrop mean "stop after this one" while the queue runs**, not
  close: unmounting mid-`/apply` would leave a half-populated agent and strand
  the user with no idea what landed.
- **Closing the summary still calls `onApplied(results, { open: false })`** — the
  agent list must refresh either way, or agents that just landed stay invisible
  until a reload. Only the navigate part is opt-in.
- `lede` / `closeLabel` / `initialDetections` exist for hosts that frame the
  picker themselves.

## Gotcha

- Types mirror the Python schema ([[migration]]); `/scan` output is POSTed to
  `/apply` verbatim, so the shapes must stay in lock-step.
