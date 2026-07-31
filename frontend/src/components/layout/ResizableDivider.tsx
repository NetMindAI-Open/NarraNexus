/**
 * @file_name: ResizableDivider.tsx
 * @description: Thin vertical drag handle that lets the user resize two
 * adjacent flex children. Parent owns the split state; this component is
 * purely the input device.
 *
 * Perf design (2026-05-14, revised 2026-07-30): the drag is split into three
 * callbacks —
 *   - `onResizeStart()` fires on pointerdown. The parent enters dragging mode
 *     (freezes iframe-hosting content so per-frame resizes can't reflow it).
 *   - `onResize(clientX)`  fires at most once per animation frame while
 *     dragging. The parent moves the panes IMPERATIVELY here (writes
 *     flex-grow / width straight to the DOM), so the panes track the cursor
 *     live and a 60 Hz drag still re-renders nothing.
 *   - `onResizeEnd(clientX)` fires once on release. The parent commits the
 *     final ratio to React state + persistence here — exactly one
 *     re-render per drag.
 *
 * Pointer capture (2026-05-14): on pointerdown we `setPointerCapture` on
 * the handle. Without it, the moment the cursor crosses over the artifact
 * pane's sandboxed `<iframe>` the iframe swallows pointermove/pointerup —
 * the drag "freezes" because our listeners never see another event.
 * Capturing the pointer to the handle forces every subsequent event for
 * that pointerId to be dispatched to the handle, iframe or not. We
 * therefore listen on the handle element itself (the capture target),
 * not on `document`.
 *
 * pointermove events are coalesced through `requestAnimationFrame`: many
 * native moves within a frame collapse into a single `onResize` call with
 * the latest clientX. Listeners are torn down atomically via
 * AbortController.
 *
 * Guaranteed teardown (2026-07-30)
 * --------------------------------
 * Reported symptom: "左右拖拽偶尔会卡住，卡顿之后再去拖会很卡", with the
 * artifact pane's content left stranded at a stale width.
 *
 * Both halves of that were one bug: the drag had exactly TWO ways to end,
 * `pointerup` and `pointercancel`, both on the handle. Miss them — release
 * over the sandboxed artifact iframe, outside the window, or after capture is
 * yanked — and nothing ever ran the teardown. Consequences compound:
 *   1. `onResizeEnd` never fires → the parent stays in dragging mode → the
 *      artifact pane keeps its frozen content width (the visible "卡住").
 *   2. `cursor: col-resize` / `user-select: none` stay welded to <body>.
 *   3. The AbortController never aborts, so the pointermove listener and its
 *      rAF loop stay attached to the handle. The NEXT drag adds another set
 *      on top, and every frame runs one more copy — hence "再去拖会很卡",
 *      getting worse with each stutter.
 *
 * So teardown is now unconditional rather than best-effort: `stop` is
 * idempotent, reachable from `pointerup` / `pointercancel` /
 * `lostpointercapture` on the handle, from window-level `pointerup` /
 * `pointercancel` / `blur` as a backstop, from the next `pointerdown`
 * (re-grab), and from unmount. Whichever fires first wins; the rest no-op.
 */

import { useCallback, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';

interface Props {
  /**
   * Fires once on pointerdown, before any move. The parent uses this to enter
   * "dragging" mode — e.g. freeze an iframe's width so the live-follow drag
   * below doesn't reflow it on every frame.
   */
  onResizeStart?: () => void;
  /** Fires ≤ once per frame during the drag. Move the panes imperatively here. */
  onResize: (clientX: number) => void;
  /** Fires once on pointer release / cancel. Commit to state here. */
  onResizeEnd: (clientX: number) => void;
  /** Optional aria label; defaults to the chat ↔ artifacts wording. */
  label?: string;
  /** Optional hover tooltip; defaults to the chat ↔ artifacts wording. */
  title?: string;
  /**
   * Replaces the default `mx-1` horizontal margin. Needed when the divider
   * sits in a flex parent that already has a `gap-*`: the gap lands on BOTH
   * sides of the handle and stacks with its own margins into an uncomfortably
   * wide blank strip. A negative margin cancels the surplus. Deliberately a
   * *replacement*, not an addition — two competing Tailwind margin classes
   * resolve by stylesheet order, not by the order written here.
   */
  marginClassName?: string;
}

export function ResizableDivider({
  onResizeStart,
  onResize,
  onResizeEnd,
  label,
  title,
  marginClassName,
}: Props) {
  const { t } = useTranslation();

  /**
   * Teardown for the drag currently in flight, or null. Idempotent, and
   * callable with no argument (it falls back to the last seen clientX).
   *
   * This ref is the fix for the 2026-07-30 "偶尔会卡住，卡顿之后再去拖会很卡"
   * report — see the "Guaranteed teardown" section in the file header.
   */
  const endActiveDragRef = useRef<((clientX?: number) => void) | null>(null);

  // A drag in flight at unmount would leave `cursor: col-resize` and
  // `user-select: none` welded onto <body>, a pending rAF, live listeners, and
  // the parent stuck in dragging mode. The divider DOES unmount mid-drag in
  // practice: it only renders while the artifact column is expanded.
  useEffect(() => () => endActiveDragRef.current?.(), []);

  const handlePointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      e.preventDefault();
      const handle = e.currentTarget;
      const pointerId = e.pointerId;

      // Re-grabbing while a previous drag is somehow still open must close it
      // first. Without this, a drag whose pointerup went missing leaks its
      // pointermove listener AND its rAF loop onto this same handle, so every
      // later drag runs one more copy of the work — which is exactly why the
      // second drag after a stutter felt dramatically worse than the first.
      endActiveDragRef.current?.(e.clientX);

      onResizeStart?.();

      // Capture the pointer so the drag survives the cursor passing over
      // the artifact <iframe> (iframes otherwise eat pointer events).
      handle.setPointerCapture(pointerId);

      const controller = new AbortController();
      const { signal } = controller;

      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';

      // rAF coalescing: native pointermove can fire several times per frame.
      // Keep only the latest clientX and flush it once per animation frame.
      let rafId = 0;
      let pendingX = e.clientX;
      let hasPending = false;
      const flush = () => {
        rafId = 0;
        if (hasPending) {
          hasPending = false;
          onResize(pendingX);
        }
      };

      // Listen on the capture target (the handle), not document — captured
      // pointer events are dispatched to the handle regardless of what DOM
      // (or iframe) sits under the cursor.
      handle.addEventListener(
        'pointermove',
        (ev: PointerEvent) => {
          pendingX = ev.clientX;
          hasPending = true;
          if (!rafId) rafId = requestAnimationFrame(flush);
        },
        { signal },
      );

      // Idempotent: several of the terminal events below legitimately fire for
      // the same release (a pointerup implies a lostpointercapture, and the
      // window backstop sees the same pointerup the handle does), and
      // `releasePointerCapture` inside here fires one more. Only the first
      // call may commit.
      let ended = false;
      const stop = (clientX?: number) => {
        if (ended) return;
        ended = true;
        endActiveDragRef.current = null;
        controller.abort();
        if (rafId) {
          cancelAnimationFrame(rafId);
          rafId = 0;
        }
        if (handle.hasPointerCapture?.(pointerId)) {
          handle.releasePointerCapture(pointerId);
        }
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        // No clientX (unmount / re-grab) → commit the last position we saw,
        // never a stale initial one.
        onResizeEnd(clientX ?? pendingX);
      };
      endActiveDragRef.current = stop;

      const stopFromEvent = (ev: Event) => stop((ev as PointerEvent).clientX);

      handle.addEventListener('pointerup', stopFromEvent, { signal });
      handle.addEventListener('pointercancel', stopFromEvent, { signal });
      // `lostpointercapture`: if capture is taken away mid-drag — the artifact
      // pane's sandboxed iframe is the suspect — the handle stops receiving
      // pointer events entirely, so pointerup would never arrive and the drag
      // would hang "active" forever. Treat losing capture as the end of it.
      handle.addEventListener('lostpointercapture', stopFromEvent, { signal });

      // Backstop, on window in the CAPTURE phase: a release the handle never
      // sees (pointer let go outside the window, over an iframe, or after the
      // tab lost focus) still has to end the drag. Belt to the braces above —
      // the failure mode being defended against is not "the drag ends late",
      // it is "the drag never ends", which welds the body cursor on and leaves
      // the artifact pane frozen at a stale width.
      window.addEventListener('pointerup', stopFromEvent, { signal, capture: true });
      window.addEventListener('pointercancel', stopFromEvent, { signal, capture: true });
      window.addEventListener('blur', () => stop(), { signal });
    },
    [onResizeStart, onResize, onResizeEnd],
  );

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label ?? t('layout.resizableDivider.ariaLabel')}
      onPointerDown={handlePointerDown}
      className={cn(
        'flex-none w-1.5 cursor-col-resize self-stretch transition-colors',
        'bg-[var(--border-default)] hover:bg-[var(--text-primary)]',
        marginClassName ?? 'mx-1',
      )}
      title={title ?? t('layout.resizableDivider.title')}
    />
  );
}

export default ResizableDivider;
