/**
 * The composer grows with its content (height follows scrollHeight; the CSS
 * max-height caps it, past which the textarea scrolls internally) and
 * shrinks back when text is removed.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { Composer } from '../Composer';

function renderComposer() {
  render(
    <Composer
      agentId="a1"
      placeholder="type"
      disabled={false}
      onSubmit={vi.fn()}
      onEmptyChange={vi.fn()}
    />,
  );
  const textarea = screen.getByPlaceholderText('type') as HTMLTextAreaElement;
  // jsdom has no layout — drive scrollHeight by line count so the effect
  // has something real to read.
  Object.defineProperty(textarea, 'scrollHeight', {
    configurable: true,
    get: () => 52 + (textarea.value.split('\n').length - 1) * 24,
  });
  return textarea;
}

describe('composer autosize', () => {
  it('recomputes on width change, not on its own height writes', () => {
    // Local ResizeObserver stub: capture the callback, drive it by hand.
    let roCallback: ((entries: Array<{ contentRect: { width: number } }>) => void) | null = null;
    const RO = class {
      constructor(cb: (entries: Array<{ contentRect: { width: number } }>) => void) {
        roCallback = cb;
      }
      observe() {}
      disconnect() {}
    };
    vi.stubGlobal('ResizeObserver', RO);
    try {
      const textarea = renderComposer();
      fireEvent.change(textarea, { target: { value: 'one\ntwo' } });
      expect(roCallback).not.toBeNull();
      // observe()'s initial delivery establishes the baseline width.
      roCallback!([{ contentRect: { width: 500 } }]);
      const before = textarea.style.height;
      // Height-only redelivery (same width) must NOT recompute…
      Object.defineProperty(textarea, 'scrollHeight', { configurable: true, get: () => 999 });
      roCallback!([{ contentRect: { width: 500 } }]);
      expect(textarea.style.height).toBe(before);
      // …while a width change does.
      roCallback!([{ contentRect: { width: 300 } }]);
      expect(textarea.style.height).toBe('999px');
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('height follows content and shrinks back', () => {
    const textarea = renderComposer();
    fireEvent.change(textarea, { target: { value: 'one\ntwo\nthree\nfour' } });
    expect(textarea.style.height).toBe(`${52 + 3 * 24}px`);
    fireEvent.change(textarea, { target: { value: 'one' } });
    expect(textarea.style.height).toBe('52px');
  });
});
