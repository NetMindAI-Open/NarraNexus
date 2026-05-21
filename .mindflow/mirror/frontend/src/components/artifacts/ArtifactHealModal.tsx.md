---
code_file: frontend/src/components/artifacts/ArtifactHealModal.tsx
last_verified: 2026-05-19
stub: false
---

# ArtifactHealModal.tsx — pick-a-candidate-to-recover dialog

## Why it exists

When `/heal` finds 0 or >1 candidate workspace files for a broken-pointer
artifact, it returns the list to the frontend instead of auto-registering.
This modal renders that list so the user picks the right entry file, and
fires `attempt(workspacePath)` on the parent's [[useArtifactHeal.ts]] hook
to register onto the chosen path. Empty list → "no matching file found"
state with no register button.

The modal is rendered *by* the renderer that triggered the heal (Chart /
Html / Csv / Markdown / Image / Pdf), not mounted at the layout level —
only one artifact tab is active at a time, so co-locating the modal with
the renderer keeps state scoped to that one artifact and avoids a global
modal store.

## Upstream / Downstream

- **Mounted by**: each renderer in `frontend/src/components/artifacts/renderers/`.
- **Driven by**: state returned from [[useArtifactHeal.ts]].
- **No direct API calls** — the hook owns the round trip.

## Design decisions

- **Radio-pick + explicit Register button**, not click-to-pick. A
  workspace file mis-match would re-register the wrong content; an extra
  click protects against the fat-finger case.

- **mtime in the candidate row**. The agent's most recent file is almost
  always the right pick (cron rebuilds the same path daily), so showing
  mtime lets the user spot the latest at a glance.

- **`useArtifactStore`-free**. The modal is a pure presentational
  component — all state lives in the parent hook. Easier to test, easier
  to relocate if we move heal into the layout later.
