---
code_file: frontend/src/components/artifacts/ArtifactZoomModal.tsx
last_verified: 2026-08-04
stub: false
---

## 2026-08-04 — ArtifactRenderer 必须传 bounded={false}

列内滚动修复给 ArtifactRenderer 加了默认的有界滚动包装盒；弹窗这里
**必须**显式传 `bounded={false}` 关掉它：sizer/inner 两层都是确定高度，
zoom 机制依赖渲染内容**溢出**这些层、由最外层 scrollRef(overflow-auto)
统一承载滚动。带上包装盒会把内容钳成一屏、在缩放层内产生第二根滚动条，
且 overscroll-contain 掐断滚动链——放大态读长文/平移宽表直接报废。
契约测试在 [[__tests__/scrollContainment.test.tsx]]（bounded=false 断言
渲染树里零滚动容器）。
# ArtifactZoomModal.tsx — Fullscreen artifact viewer

## Why it exists

User feedback (2026-05-14): the embedded artifact column is great for
"keep an eye on it while chatting", but for inspecting a complex chart,
a long HTML report, or a multi-page PDF the column is too narrow. Add a
zoom affordance — click on any tab (or its Maximize icon) to pop the
artifact into a near-fullscreen overlay with the rest of the page dimmed
and blurred behind it.

## Why not reuse `ui/Dialog`

`ui/Dialog` already provides portal + Esc + scroll-lock plumbing, but it
caps at `max-w-6xl` (too small for chart fidelity) and uses an opaque
ink backdrop. The user explicitly asked for a blurred backdrop so the
rest of the UI stays visually present behind the zoom. Reusing Dialog
would have required adding two flag props (`fullscreen?`, `blur?`) just
for this one consumer, which felt like API bloat for a single caller.
The dedicated component shares the portal/Esc patterns but specializes
on the artifact-viewing use case.

## Upstream / Downstream

- **Rendered by**: `ArtifactColumn` (state: `zoomedId`, setter passed to
  TabStrip). ArtifactColumn **keys this component by `artifact_id`** —
  see "Content zoom" below for why.
- **Renders**: `ArtifactRenderer` (shared dispatcher) plus
  `ArtifactDownloadMenu` in the header so the user can still grab the
  artifact while it's zoomed.
- **Closed by**: backdrop click, Esc key, or X button. All converge on
  the same `onClose` callback.

## Layout

- `position: fixed inset-0`, `z-[60]` — sits above the `z-50` of
  `ui/Dialog` so a zoom on top of a Dialog is at least *visible* (not a
  supported flow, but won't disappear).
- Frame: `95vw × 95vh`, flat ink border, no border-radius — matches the
  Nordic archive visual language of `ui/Dialog`.
- Header: title + zoom controls (−/%/+) + Download menu + X.
- Body: a `overflow-auto` scroll container wrapping a `zoom`-styled div
  that wraps the renderer.

## Content zoom (2026-05-14, 2 iterations)

User asked for the content inside the fullscreen frame to be scalable.
Range 0.25x–3x, driven by header +/- buttons, `Ctrl/Cmd + wheel`, and
`Ctrl/Cmd +/-/0` keys.

- **`transform: scale()`, not CSS `zoom`.** The first cut used CSS
  `zoom` (it reflows, so scrollbars track for free) — but `zoom` is
  unreliable on `<iframe>` across engines, and the HTML artifact
  renderer *is* a sandboxed iframe. The buttons appeared to "do
  nothing" because the iframe didn't visually scale. `transform:
  scale()` is GPU-composited and works on every artifact kind
  including the iframe.
- **Two-layer wrapper to make scroll work.** `transform` keeps the
  element's layout box at its *unscaled* size, so a single wrapper
  would never give the `overflow-auto` container anything to scroll.
  The body nests:
  - *sizer* — `width/height = zoom·100%` → reserves the scaled footprint
    so scrollbars appear at zoom > 1.
  - *inner* — `width/height = (100/zoom)%` of the sizer (so its layout
    box resolves back to exactly the container size) + `scale(zoom)` +
    `origin-top-left`. The artifact (incl. the iframe, `w-full/h-full`)
    fills `inner` and scales with it.
- **Wheel zoom uses a native non-passive listener** (attached via
  `scrollRef` in a `useEffect`), not React's `onWheel` — React's
  synthetic wheel handler is passive, so `preventDefault()` there is a
  no-op and the browser's own page zoom would fire instead.
- **Zoom reset = remount, not an effect.** `ArtifactColumn` passes
  `key={zoomed?.artifact_id ?? 'closed'}`. Opening a different artifact
  changes the key → React remounts → `useState(1)` runs fresh. This
  sidesteps the `react-hooks/set-state-in-effect` lint rule and is the
  React-idiomatic "reset all state on identity change" pattern. The
  cost — `ArtifactRenderer` remounts and re-suspends — is negligible
  because the lazy chunk is already cached after first use.

## Scroll lock

`document.body.style.overflow = 'hidden'` while open, restored from a
captured `prevOverflow` on close. The capture-and-restore is deliberate
— other modals (Dialog, QuotaExceededModal) also touch this property,
and if two modals stack we don't want close-order to leak `''` over a
still-needed `'hidden'`.

## Gotcha

The portal target is `document.body`. If the SSR context changes (it
doesn't today — Vite SPA), `createPortal` to `document.body` would error.
The whole file is a no-op render when `artifact === null`, so the
returns happen before any DOM access.
