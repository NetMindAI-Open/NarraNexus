---
code_file: frontend/src/components/artifacts/ArtifactDownloadMenu.tsx
last_verified: 2026-06-16
stub: false
---

# ArtifactDownloadMenu.tsx — Per-artifact download / export dropdown

## Why it exists

The small download/export affordance in the artifact column header (and in
`[[ArtifactZoomModal]]`). For chart artifacts it offers PNG/JPEG export (via the
live ECharts instance registered in `artifactStore.chartInstances`) plus the
raw JSON; for everything else, just "Download original" against the
token-protected raw URL minted by `useArtifactRawUrl`.

## 上下游关系
- **被谁用**: `[[ArtifactColumn]]` (header toolbar), `[[ArtifactZoomModal]]` (header).
- **依赖谁**: `useArtifactStore` (chart instances), `useArtifactRawUrl` (signed URL).

## 设计决策

**Portal-mounted panel (2026-05-20 rewrite)**: the dropdown is rendered through
`createPortal(..., document.body)` and positioned with `fixed` coordinates
derived from the trigger button's `getBoundingClientRect()` (right-aligned to
the trigger's right edge, 4px below it). This is **not optional polish** — every
ancestor of the artifact column (`MainLayout` `<main>`/group, `ArtifactColumn`
`<aside>` and its content div) sets `overflow-hidden` for flex-sizing
correctness. The previous implementation used a native `<details>/<summary>`
with an `absolute right-0 top-full` child, which got clipped by that
overflow chain down to a tiny sliver — the "small weird window" bug. Portal is
the same escape hatch `[[ArtifactZoomModal]]` and `ui/Dialog` use. `z-index`
alone does **not** fix overflow clipping — they're independent.

**Controlled open state**: `useState(open)` + click-outside (`mousedown` on
document, ignoring clicks inside trigger/menu) + Escape to close, replacing the
uncontrolled `<details>` toggle. Position recomputes on `scroll` (capture phase,
so inner overflow containers count) and `resize` while open.

## 2026-06-16 — "Download original" now calls downloadFile()

The "Download original" entry previously used a raw `<a href download>`
against the token-protected URL returned by `useArtifactRawUrl`. This
silently failed on both the DMG (WKWebView mixed-content block) and
`bash run.sh` (cross-origin, `download` attribute ignored). It now
calls `downloadFile({ url, filename })` from `lib/download.ts`, which
picks the correct strategy per runtime surface. Artifact raw URLs are
public (access token in query string), so `authHeaders` is omitted.

Chart PNG/JPEG export (using a `data:` URL from the live ECharts
canvas instance) is unchanged — that path never hits a backend endpoint
and does not suffer from cross-origin or mixed-content issues.

## Gotcha / 边界情况

- Chart export reads `chartInstances[artifact_id]` lazily via
  `useArtifactStore.getState()` at click time; if the chart hasn't mounted yet
  it alerts "still loading" rather than failing silently.
- `right` is computed as `window.innerWidth - rect.right`; if the trigger ever
  sits near the right viewport edge with a >200px menu, the menu stays pinned to
  the trigger's right edge (acceptable — the menu has room to the left).
