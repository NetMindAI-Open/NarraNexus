---
code_file: frontend/src/components/layout/ResizableDivider.tsx
last_verified: 2026-05-14
stub: false
---

# ResizableDivider.tsx — Vertical drag handle between two flex panes

## Why it exists

User asked for the Chat ↔ Artifacts split to be user-adjustable
(2026-05-14). Rather than pull in a third-party split-pane lib (most
ship their own styling, focus management, and accessibility quirks)
this tiny component owns the drag mechanics only; the parent owns the
split state and decides what fraction of its container the divider
should set.

## Upstream / Downstream

- **Used by**: `MainLayout.ChatView` (between the chat column and
  `ArtifactColumn`).
- **API**: two callbacks, both receiving raw `clientX` so the parent
  translates to its own container-relative ratio:
  - `onResize(clientX)` — fires ≤ once per animation frame during drag.
    Parent updates the DOM **imperatively** here.
  - `onResizeEnd(clientX)` — fires once on release. Parent commits to
    React state + persistence here.

## Perf design — why two callbacks (2026-05-14)

This component stays deliberately dumb: it never touches the columns.
The two-callback split lets the parent keep React (and any layout
reflow) out of the per-frame drag path.

- `onResize` is rAF-coalesced inside this component — many native moves
  per frame collapse into one call with the latest `clientX`. The
  parent (`[[MainLayout]]`) handles it by moving only a thin "ghost"
  preview line imperatively — the real panes don't move, so nothing
  reflows mid-drag.
- `onResizeEnd` fires once on release; the parent does a single
  `setChatSplit` → one re-render → the panes resize and their content
  reflows exactly once.

Net: zero React renders *and* zero pane reflow during the drag; one of
each on release. (See `[[MainLayout]]` "Resize perf" for the two
iterations this went through — an earlier cut moved the panes
imperatively per frame, which still reflowed the artifact iframe.)

## Pointer capture — the iframe-eats-events fix (2026-05-14)

**The bug this solves:** dragging the divider rightward (shrinking the
artifact pane) froze the moment the cursor crossed over the artifact
pane's content — but only when that content was an HTML artifact (a
sandboxed `<iframe>`). Dragging leftward, or rightward with the cursor
kept on the tab-strip row, worked fine.

**Why:** an `<iframe>` is its own event target. Once the cursor is over
it, `pointermove` / `pointerup` are dispatched into the iframe's
document, never reaching a listener in the parent document. The drag
listeners stop receiving events → the drag "freezes".

**Fix:** `handle.setPointerCapture(e.pointerId)` on `pointerdown`. A
captured pointer has *all* its subsequent events dispatched to the
capture target (the handle), regardless of what element — iframe
included — sits under the cursor. Capture auto-releases on
`pointerup` / `pointercancel`; `stop` also releases explicitly (guarded
by `hasPointerCapture`) for clarity.

## Drag lifecycle

`pointerdown` captures the pointer (above), opens an `AbortController`,
and attaches `pointermove` / `pointerup` / `pointercancel` listeners **on
the handle element itself** — that is the capture target, so it is
guaranteed to receive the captured events. (Pre-capture this listened on
`document`; with capture, the handle is the correct target.) Any
terminal event aborts the controller (removes all listeners atomically),
cancels any in-flight rAF, and releases pointer capture.

While dragging the component force-sets `document.body.style.cursor`
to `col-resize` and `userSelect` to `none`, restoring both on release.
The body-level cursor override means the cursor stays consistent even
when the pointer leaves the 6 px hit-zone of the handle itself.

## Gotcha

The handle is `w-1.5 mx-1` — 6 px wide with 4 px margin each side, total
14 px hit zone. Smaller would feel un-grabbable; larger would visually
intrude into the chat / artifact gutter. The flex parent must use
`gap-0` (not `gap-N`) in the chat-artifact group, otherwise the gap
stacks on top of the divider's margins and creates an uncomfortably
wide blank strip.
