/**
 * @file_name: ResizableDivider.tsx
 * @description: Thin vertical drag handle that lets the user resize two
 * adjacent flex children. Parent owns the split state; this component is
 * purely the input device.
 *
 * Perf design (2026-05-14): the drag is split into two callbacks —
 *   - `onResize(clientX)`  fires at most once per animation frame while
 *     dragging. The parent moves a lightweight preview indicator here
 *     (no React state), so a 60 Hz drag doesn't re-render anything.
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
 * AbortController on pointerup / pointercancel.
 */

import { useCallback } from 'react';
import { useTranslation } from 'react-i18next';

interface Props {
  /** Fires ≤ once per frame during the drag. Move the preview indicator here. */
  onResize: (clientX: number) => void;
  /** Fires once on pointer release / cancel. Commit to state here. */
  onResizeEnd: (clientX: number) => void;
  /** Optional aria label, defaults to a sensible English string. */
  label?: string;
}

export function ResizableDivider({ onResize, onResizeEnd, label }: Props) {
  const { t } = useTranslation();
  const handlePointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      e.preventDefault();
      const handle = e.currentTarget;
      const pointerId = e.pointerId;

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

      const stop = (ev: PointerEvent) => {
        controller.abort();
        if (rafId) {
          cancelAnimationFrame(rafId);
          rafId = 0;
        }
        if (handle.hasPointerCapture(pointerId)) {
          handle.releasePointerCapture(pointerId);
        }
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        onResizeEnd(ev.clientX);
      };
      handle.addEventListener('pointerup', stop, { signal });
      handle.addEventListener('pointercancel', stop, { signal });
    },
    [onResize, onResizeEnd],
  );

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label ?? t('layout.resizableDivider.ariaLabel')}
      onPointerDown={handlePointerDown}
      className="flex-none w-1.5 mx-1 cursor-col-resize bg-[var(--border-default)] hover:bg-[var(--text-primary)] transition-colors self-stretch"
      title={t('layout.resizableDivider.title')}
    />
  );
}

export default ResizableDivider;
