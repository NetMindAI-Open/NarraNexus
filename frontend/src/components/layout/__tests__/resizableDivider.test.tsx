/**
 * @file_name: resizableDivider.test.tsx
 * @date: 2026-07-30
 * @description: Drag lifecycle tests for ResizableDivider — specifically that
 * the drag ALWAYS ends.
 *
 * Regression: the drag used to end only on `pointerup` / `pointercancel`
 * dispatched to the handle. When neither arrived (release over the artifact
 * pane's sandboxed iframe, release outside the window, capture yanked away)
 * the teardown never ran, which left the body cursor welded on, the artifact
 * pane frozen at a stale width, and — worst — the pointermove listener plus
 * its rAF loop still attached. The next drag stacked another set on top, so
 * each stutter made dragging measurably slower. Reported as "偶尔会卡住,
 * 卡顿之后再去拖会很卡".
 *
 * Every test here asserts one of the paths that must reach the teardown.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, cleanup } from '@testing-library/react';
import { ResizableDivider } from '../ResizableDivider';

// jsdom implements neither pointer capture nor PointerEvent's pointerId
// plumbing, so stub the three methods the component calls.
beforeEach(() => {
  const captured = new Set<number>();
  Element.prototype.setPointerCapture = function (id: number) {
    captured.add(id);
  };
  Element.prototype.releasePointerCapture = function (id: number) {
    captured.delete(id);
  };
  Element.prototype.hasPointerCapture = function (id: number) {
    return captured.has(id);
  };
});

afterEach(() => {
  cleanup();
  document.body.style.cursor = '';
  document.body.style.userSelect = '';
});

function setup() {
  const onResizeStart = vi.fn();
  const onResize = vi.fn();
  const onResizeEnd = vi.fn();
  const view = render(
    <ResizableDivider
      onResizeStart={onResizeStart}
      onResize={onResize}
      onResizeEnd={onResizeEnd}
    />,
  );
  const handle = view.getByRole('separator');
  return { ...view, handle, onResizeStart, onResize, onResizeEnd };
}

describe('ResizableDivider — the drag always ends', () => {
  it('ends on a normal pointerup, restoring the body styles', () => {
    const { handle, onResizeEnd } = setup();

    fireEvent.pointerDown(handle, { pointerId: 1, clientX: 500 });
    expect(document.body.style.cursor).toBe('col-resize');

    fireEvent.pointerUp(handle, { pointerId: 1, clientX: 620 });

    expect(onResizeEnd).toHaveBeenCalledTimes(1);
    expect(onResizeEnd).toHaveBeenCalledWith(620);
    expect(document.body.style.cursor).toBe('');
    expect(document.body.style.userSelect).toBe('');
  });

  it('ends when pointer capture is lost — the iframe case', () => {
    const { handle, onResizeEnd } = setup();

    fireEvent.pointerDown(handle, { pointerId: 1, clientX: 500 });
    fireEvent(handle, new Event('lostpointercapture'));

    expect(onResizeEnd).toHaveBeenCalledTimes(1);
    expect(document.body.style.cursor).toBe('');
  });

  it('ends on a window-level pointerup the handle never sees', () => {
    const { handle, onResizeEnd } = setup();

    fireEvent.pointerDown(handle, { pointerId: 1, clientX: 500 });
    // Released somewhere else entirely — e.g. over a sandboxed iframe.
    fireEvent.pointerUp(document.body, { pointerId: 1, clientX: 700 });

    expect(onResizeEnd).toHaveBeenCalledTimes(1);
    expect(document.body.style.cursor).toBe('');
  });

  it('ends when the window loses focus mid-drag', () => {
    const { handle, onResizeEnd } = setup();

    fireEvent.pointerDown(handle, { pointerId: 1, clientX: 500 });
    fireEvent.blur(window);

    expect(onResizeEnd).toHaveBeenCalledTimes(1);
    expect(document.body.style.cursor).toBe('');
  });

  it('ends when the divider unmounts mid-drag', () => {
    const { handle, onResizeEnd, unmount } = setup();

    fireEvent.pointerDown(handle, { pointerId: 1, clientX: 500 });
    unmount();

    expect(onResizeEnd).toHaveBeenCalledTimes(1);
    expect(document.body.style.cursor).toBe('');
    expect(document.body.style.userSelect).toBe('');
  });

  it('commits only once even though several terminal events fire', () => {
    const { handle, onResizeEnd } = setup();

    fireEvent.pointerDown(handle, { pointerId: 1, clientX: 500 });
    // A real release produces all of these in some order.
    fireEvent.pointerUp(handle, { pointerId: 1, clientX: 640 });
    fireEvent(handle, new Event('lostpointercapture'));
    fireEvent.pointerUp(document.body, { pointerId: 1, clientX: 640 });

    expect(onResizeEnd).toHaveBeenCalledTimes(1);
  });
});

describe('ResizableDivider — no listener accumulation across drags', () => {
  it('closes an orphaned drag when the handle is grabbed again', () => {
    const { handle, onResizeEnd } = setup();

    // Drag 1 — its release goes missing entirely.
    fireEvent.pointerDown(handle, { pointerId: 1, clientX: 500 });
    fireEvent.pointerMove(handle, { pointerId: 1, clientX: 560 });
    expect(onResizeEnd).not.toHaveBeenCalled();

    // Drag 2 — grabbing again must close drag 1 first.
    fireEvent.pointerDown(handle, { pointerId: 2, clientX: 600 });
    expect(onResizeEnd).toHaveBeenCalledTimes(1);

    fireEvent.pointerUp(handle, { pointerId: 2, clientX: 660 });
    expect(onResizeEnd).toHaveBeenCalledTimes(2);
    expect(onResizeEnd).toHaveBeenLastCalledWith(660);
  });

  it('an orphaned drag stops driving onResize once a new drag starts', () => {
    const { handle, onResize } = setup();

    fireEvent.pointerDown(handle, { pointerId: 1, clientX: 500 });
    fireEvent.pointerDown(handle, { pointerId: 2, clientX: 600 });

    // One live drag → one onResize per move, not two. Before the teardown fix
    // the orphaned listener stayed attached and every frame did double work.
    onResize.mockClear();
    fireEvent.pointerMove(handle, { pointerId: 2, clientX: 640 });

    // rAF-coalesced: flush the frame, then count.
    return new Promise<void>((resolve) => {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          expect(onResize).toHaveBeenCalledTimes(1);
          resolve();
        });
      });
    });
  });
});
