---
code_file: frontend/src/hooks/useArtifactHeal.ts
last_verified: 2026-05-27
stub: false
---

## 2026-05-27 — dismissed-latch + memoized controller (Dismiss-loop fix)

P0 bug 2026-05-25 (Jiaxi Chen): the "no matching file" modal was
impossible to close — clicking Dismiss closed the modal but the
renderer's HEAD-410 useEffect refired immediately, called `attempt()`
again, and reopened it. Two compounding causes:

1. The hook returned a fresh object literal every render, so renderer
   deps like `[url, heal]` churned on every state change.
2. There was no record of "user already said no" — every attempt()
   call that produced no candidates opened the modal again.

Fix:
- `dismissedRef` latches when the user calls `dismiss()`. Subsequent
  `attempt()` calls suppress `setModalOpen(true)`. Reset when
  `agentId/artifactId` changes (different artifact = fresh chance).
- The returned controller is wrapped in `useMemo` keyed on its
  primitive/stable members, so its identity is stable across renders
  when no state changed. Consumer effects with `[url, heal]` deps no
  longer churn.
- `busyRef` mirrors `busy` so `attempt`'s `useCallback` no longer
  depends on `busy` — keeps `attempt` stable, which keeps the memo
  stable, which keeps consumer effects from re-firing.

Renderers still take a belt-and-braces ref to `attempt` and depend
only on `[url]` in their load effects — see HtmlRenderer.tsx.md.

Tested by `frontend/src/hooks/__tests__/useArtifactHeal.test.tsx`.

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

- **Don't put the whole controller into a renderer effect's deps.** Put
  `attempt` in a ref and depend on `[url]` only. Even with the controller
  memo'd, any internal state change (busy/modalOpen/etc) re-memoes and
  re-fires the effect — that costs an extra HEAD per state transition.
