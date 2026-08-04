---
code_file: frontend/src/components/artifacts/ArtifactRenderer.tsx
last_verified: 2026-08-04
stub: false
---

## 2026-08-04 — bounded 开关：列内包装盒拥有全部滚动，弹窗零包装

云端 artifact 滑不动修复（Base recvpm05jsLg3o）。新增 `bounded` prop
（默认 true）：

- **bounded=true（列内）**：分发出口包一层 `h-full w-full overflow-auto
  overscroll-contain` 盒——列内容盒 overflow-hidden 只裁剪不滚动（拖拽
  冻结机制依赖它），渲染器根节点全部 auto 尺寸，所以这层盒子是列内
  **唯一**的滚动容器，纵向和宽表横向都归它。也顺带消掉了 chart 分支
  （absolute inset-0）与非 chart 分支的高度约束不对称。
- **bounded=false（ZoomModal 传入）**：不包任何盒子。弹窗的 sizer/inner
  缩放层是确定高度，滚动必须由弹窗最外层 overflow-auto 承载（zoom 机制
  依赖内容溢出这些层）；任何有界盒/渲染器级 h-full 都会把内容钳成一屏
  并在缩放层内嵌套第二根滚动条——第一版 review 打回的正是这个。

**绝不能用 absolute inset-0 当包装**：本组件在弹窗缩放层里没有
positioned 祖先，absolute 会逃逸破坏 zoom 平移。契约测试
[[__tests__/scrollContainment.test.tsx]]。

## 2026-07-22 — application/x-url → UrlRenderer

Dispatch table gained `application/x-url` → lazy `UrlRenderer` (see
[[UrlRenderer.tsx]]). Same office-live extension pattern (union + table +
renderer).
## 2026-05-14 — drop `version` prop from RendererComponent

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
