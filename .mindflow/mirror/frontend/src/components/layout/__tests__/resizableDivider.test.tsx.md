---
code_file: frontend/src/components/layout/__tests__/resizableDivider.test.tsx
last_verified: 2026-07-30
stub: false
---

# resizableDivider.test.tsx — every path out of a drag

## 为什么存在

[[ResizableDivider]] shipped with a drag that could silently never end (see
that file's 2026-07-30 (2) entry). The damage was invisible in isolation —
nothing threw, nothing logged — and only surfaced as "拖拽偶尔会卡住,之后很卡"
after the leaked listeners had accumulated. So the property worth testing is
not "a drag works"; it is **"a drag cannot fail to end"**.

One test per exit: handle `pointerup`, `lostpointercapture`, window-level
`pointerup`, window `blur`, unmount, and re-grab. Plus two that pin the
consequences: `onResizeEnd` commits exactly once even when several terminal
events fire for the same release, and an orphaned drag stops driving `onResize`
once a new one starts (the listener-accumulation regression itself).

6 of the 8 cases fail against the pre-fix implementation — verified, not
assumed.

## 上下游关系

- **测的是**: [[ResizableDivider]] only. The parent's use of the callbacks
  ([[MainLayout]] `dragging` → [[ArtifactColumn]] `contentFrozen`) is not
  exercised here; these tests just guarantee `onResizeEnd` always arrives, which
  is what unfreezes that chain.

## 新人易踩的坑

jsdom implements **neither** pointer capture nor `PointerEvent`'s capture
plumbing, so `beforeEach` stubs `setPointerCapture` /
`releasePointerCapture` / `hasPointerCapture` on `Element.prototype` with a
`Set`. Without those stubs `pointerdown` throws before any assertion runs.

`onResize` is rAF-coalesced, so the accumulation test has to wait two
animation frames before counting calls — a synchronous assertion right after
`pointerMove` sees zero and passes vacuously.

`afterEach` resets `document.body.style` explicitly: a test that intentionally
leaves a drag open would otherwise leak `cursor: col-resize` into whatever
test module runs next.
