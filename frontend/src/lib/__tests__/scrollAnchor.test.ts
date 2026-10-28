/**
 * Prepend anchoring: after older history loads above the viewport, the item
 * the user was looking at must not move. Height-delta restore alone lands
 * the user on the new chunk whenever anything else changes height around
 * the prepend (loading row, images) — the element anchor is the truth.
 */

import { describe, it, expect } from 'vitest';
import { capturePrependAnchor, restorePrependAnchor } from '../scrollAnchor';

function fakeAnchor(tops: number[], connected = true) {
  let i = 0;
  return {
    getBoundingClientRect: () => ({ top: tops[Math.min(i++, tops.length - 1)] }),
    isConnected: connected,
  };
}

describe('scrollAnchor', () => {
  it('shifts scrollTop by exactly how far the anchored item moved', () => {
    const container = { scrollTop: 40, scrollHeight: 1000 };
    const anchor = fakeAnchor([120, 870]); // item pushed down 750px by the prepend
    const captured = capturePrependAnchor(container, anchor);
    container.scrollHeight = 1780; // includes an unrelated +30px loading row
    restorePrependAnchor(container, captured);
    expect(container.scrollTop).toBe(40 + 750);
  });

  it('falls back to the height delta when nothing was rendered yet', () => {
    const container = { scrollTop: 0, scrollHeight: 500 };
    const captured = capturePrependAnchor(container, null);
    container.scrollHeight = 1300;
    restorePrependAnchor(container, captured);
    expect(container.scrollTop).toBe(800);
  });

  it('falls back when the anchor left the DOM before restore', () => {
    const container = { scrollTop: 10, scrollHeight: 500 };
    const anchor = fakeAnchor([100], false);
    const captured = capturePrependAnchor(container, anchor);
    container.scrollHeight = 900;
    restorePrependAnchor(container, captured);
    expect(container.scrollTop).toBe(10 + 400);
  });
});
