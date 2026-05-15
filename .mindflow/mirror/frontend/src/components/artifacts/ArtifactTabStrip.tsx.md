---
code_file: frontend/src/components/artifacts/ArtifactTabStrip.tsx
last_verified: 2026-05-14
stub: false
---

## 2026-05-14-r3 — delete popup simplified to a single confirm + notice

The two-state checkbox dialog (delete tab only / delete tab + files) is
gone. Deletion is registry-only end-to-end (see the agents_artifacts
backend mirror md for the rationale). The dialog now just confirms the
tab removal and tells the user where to clean up workspace files if they
want to — "use the workspace section of the config panel".

## 2026-05-14 — delete-source popup (pointer model)

Spec: `reference/self_notebook/specs/2026-05-14-artifact-pointer-model-design.md`

The 🗑️ button no longer fires `window.confirm` + immediate delete. It opens
an inline `Dialog` with a checkbox:

> [ ] Also delete the workspace source files

Off (default) → `delete(agentId, artifactId, false)` — drop the DB row,
keep the agent's working files. On → `delete(agentId, artifactId, true)` —
also rmtree the artifact root in the agent's workspace. The confirm
button label flips to make the choice obvious ("Delete tab only" vs
"Delete tab + files").

## 2026-05-14 — Zoom affordance

New required prop `onZoom(artifactId)` (provided by `[[ArtifactColumn]]`).
Surfaces two ways:

- `Maximize2` icon button on each tab, between title and the minimize
  button.
- Double-click on the tab body — `onDoubleClick` calls `onZoom`. Tooltip
  on the tab body advertises both interactions.

Single click still calls `setActive` (preserves the original tab-as-
selector UX); zoom is strictly opt-in.

# ArtifactTabStrip.tsx — Horizontal tab bar for artifact navigation

## Why it exists

`ArtifactColumn` needs a compact multi-tab navigation control that shows all open artifacts for the current agent session, allows the user to switch between them, and exposes pin and close actions per tab. The strip is the sole navigation surface for the artifact column — there is no sidebar list or dropdown alternative.

## Upstream / Downstream

- **Rendered by**: `ArtifactColumn` at the top of the content area.
- **Reads from**: `artifactStore` — `artifacts[]`, `activeArtifactId`, `setActive`, `pin`, `delete`.
- **Writes to**: `artifactStore` via `setActive` (tab click), `pin` (pin button), `delete` (close button).

## Pin emoji semantics

The pin/unpin affordance uses two emoji characters chosen for their visual metaphor:
- `📌` (pinned): the pushpin appears embedded — "this item is stuck in place." Shown when `artifact.pinned === true`.
- `📍` (round pushpin): looks like it is about to be planted — "you can pin this." Shown when `artifact.pinned === false`.

This avoids needing a separate icon library import for a minor control. The emoji render consistently at 12px in all major browsers.

## Event propagation

Each tab `<div>` has an `onClick` that calls `setActive`. The pin and close `<button>` elements inside each tab call `e.stopPropagation()` so their actions do not also trigger tab activation. Without stop-propagation, clicking "delete" would activate the tab one frame before it is removed — causing a flash and a stale `activeArtifactId` in the store.

## Design decisions

**`overflow-x-auto` on the strip container**: When many artifacts are open, the strip overflows horizontally rather than wrapping. This preserves the height of the strip at exactly one tab row, which keeps the ArtifactColumn header a fixed height that the layout can rely on.

**No virtualization**: The typical agent session produces a handful of artifacts. Full virtualization of the tab strip would add complexity with no measurable benefit. If sessions with 50+ artifacts become common, a dropdown overflow menu would be the right upgrade, not virtualization.

**`border-b border-[var(--border-default)]`** on the strip container — matches the visual weight of other column header separators in the app. The border sits between the tab strip and the renderer area.

## Gotchas

`ArtifactTabStrip` renders an "empty" message (`No artifacts yet`) when `artifacts.length === 0`. In practice, `ArtifactColumn` returns `null` before rendering the strip when there are no artifacts, so this empty state is a safety fallback that should never be visible to users. If the visibility logic in `ArtifactColumn` changes, this fallback becomes important.
