---
code_file: frontend/src/components/artifacts/ArtifactRenderer.tsx
last_verified: 2026-07-13
stub: false
---

## 2026-05-14 — drop `version` prop from RendererComponent

Spec: `reference/self_notebook/specs/2026-05-14-artifact-pointer-model-design.md`

`RendererComponent` props are now `{ artifact: Artifact }` — renderers each
mint their own view token via `useArtifactRawUrl`, so the dispatcher no
longer passes a `version` prop down. There is no version concept under the
pointer model.

# ArtifactRenderer.tsx — Shared kind → renderer dispatcher

## Why it exists

Two surfaces now display artifact bodies:

1. `ArtifactColumn` — embedded in the 4-column app shell.
2. `ArtifactZoomModal` — a fullscreen overlay opened from a tab's zoom
   button.

Before this file existed the lazy renderer table lived inline inside
`ArtifactColumn`, so the zoom modal would have had to duplicate the
`RENDERER_BY_KIND` map plus its lazy imports — a guaranteed drift hazard
when a new artifact kind lands. Extracting the dispatch into one place
keeps "list of supported kinds" in a single source of truth.

## Upstream / Downstream

- **Rendered by**: `ArtifactColumn`, `ArtifactZoomModal`.
- **Lazy imports**: `HtmlRenderer`, `ChartRenderer`, `CsvRenderer`,
  `ImageRenderer`, `MarkdownRenderer`, `PdfRenderer` (all under
  `./renderers/`).

## Lazy chunk sharing

Both call sites use the same module-level `lazy(() => import('./renderers/X'))`
expressions. React.lazy memoises by the import call site identity, so the
zoom modal and the embedded column share a single chunk per kind — opening
the zoom modal does NOT trigger a re-download of the chart bundle that the
embedded column already loaded.

## Unsupported kinds

`ArtifactKind` is a closed union in `@/types/artifact`, but the runtime
payload can in principle carry a kind we haven't wired up. The component
returns a plain-text "Unsupported artifact kind: …" fallback instead of
crashing, so a backend that emits a new kind ahead of the frontend release
just gets a soft degradation.

## Gotcha

The renderer expects an `artifact` object with a `latest_version` field
(passed as a `version` prop down to each renderer for cache-busting). If
the artifact shape changes, every renderer signature below needs updating
in lockstep — but that's a renderer contract, not this dispatcher's
concern.

## 2026-07-13 — office-live 渲染器

`RENDERER_BY_KIND` 新增 `application/vnd.officecli-live` → `OfficeWatchViewer`(懒加载)。office 文档因此走和其它 artifact 完全一样的通道(平级 tab / 最小化 / 放大 / 删除 / DB 持久化),只是渲染成实时预览而非静态文件。
