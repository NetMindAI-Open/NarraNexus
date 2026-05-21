---
code_file: frontend/src/hooks/useArtifactHeal.ts
last_verified: 2026-05-19
stub: false
---

# useArtifactHeal.ts — shared self-heal driver for artifact renderers

## Why it exists

Every artifact renderer (Chart / Html / Csv / Markdown / Image / Pdf) needs the
same response when its raw fetch returns 410: ask the backend `/heal` endpoint
to recover the pointer, and either auto-reload or surface a candidate list to
the user. Inlining that state machine into each renderer would mean six
near-identical copies and six near-identical bug surfaces.

The hook owns:

- `attempt(entryPath?)` — one round trip to `POST /agents/{aid}/artifacts/{aid}/heal`.
  No arg → server runs its workspace-scan heuristic. With an arg → server
  re-registers onto the caller-chosen path. While `busy` is true subsequent
  calls are dropped (idempotency).
- `recoveryVersion: number` — bumped on successful recovery. Renderers wire
  this to a `reload()` they get back from `useArtifactRawUrl`, so the URL
  re-mints and the data re-fetches.
- `modalOpen / candidates / message / dismiss` — drives `<ArtifactHealModal>`.

## Upstream / Downstream

- **Used by**: every renderer under `frontend/src/components/artifacts/renderers/`.
- **Calls**: `artifactsApi.heal()` → `POST /api/agents/{aid}/artifacts/{aid}/heal`.
- **Writes to**: `useArtifactStore.upsert()` so the recovered artifact's
  new size/file_path lands in the store before the next render.

## Design decisions

- **Renderer owns the detection**, hook owns the response. Only the renderer
  knows which HTTP status came back (fetchArtifactText / iframe HEAD probe /
  blob fetch all surface 410 differently). The hook just owns the round-trip.

- **`recoveryVersion` instead of returning a "did it work" flag.** Hooks
  shouldn't push imperative reload calls into their parents — bumping a
  number that the parent already uses as a `useEffect` dep keeps the
  reactive contract.

## Gotchas

- The hook does NOT auto-call `attempt()` on mount. The renderer's catch
  block must call it. Reason: we only want to trigger heal on a 410, not on
  every transient network blip.

- `busy` guards re-entry but does NOT serialise across hook instances —
  two renderers for the same artifact would each fire their own heal. In
  practice ArtifactColumn only mounts one renderer at a time per artifact,
  so this isn't a real race.
