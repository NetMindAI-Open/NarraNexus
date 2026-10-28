/**
 * @file_name: scrollStickiness.test.ts
 * @description: Whether new messages may take the scroll position.
 *
 * The team room called `scrollIntoView` unconditionally on every message count
 * change. In a room where six agents may answer at once, that means a user
 * scrolled up reading what someone said two minutes ago is yanked to the bottom
 * every few seconds — the room is least readable exactly when it is busiest.
 *
 * The private chat has had the rule for a long time (auto-scroll only while the
 * viewport is already near the bottom). It lived inline in a JSX handler, so
 * the team room could not reuse it without copying it — and this codebase has
 * just spent a whole branch on what copied rules do.
 *
 * The thresholds are the private chat's, unchanged: 100px of slack for "near
 * the bottom" and 50px for "near the top". They are not derived from anything;
 * they are the numbers the product already behaves by, and changing them here
 * would make the two surfaces feel different for no stated reason.
 */
import { describe, expect, test } from 'vitest';

import { isNearBottom, isNearTop } from '../scrollStickiness';

function viewport(scrollTop: number, clientHeight = 500, scrollHeight = 2000) {
  return { scrollTop, clientHeight, scrollHeight } as never;
}

describe('isNearBottom', () => {
  test('true when pinned to the bottom', () => {
    expect(isNearBottom(viewport(1500))).toBe(true);
  });

  test('true within the slack, so a half-line of drift still follows along', () => {
    expect(isNearBottom(viewport(1450))).toBe(true);
  });

  test('false once the user has genuinely scrolled up to read', () => {
    expect(isNearBottom(viewport(400))).toBe(false);
  });

  test('true for a transcript shorter than the viewport', () => {
    // Nothing to scroll: an empty or near-empty room must still follow new
    // messages, or the first reply in a fresh room would never come into view.
    expect(isNearBottom(viewport(0, 500, 300))).toBe(true);
  });

  test('a missing element is treated as "follow"', () => {
    // Before the first paint there is no viewport. Defaulting to "do not
    // follow" would make the room open scrolled to the top.
    expect(isNearBottom(null)).toBe(true);
  });
});

describe('isNearTop', () => {
  test('true at the very top, where older history should load', () => {
    expect(isNearTop(viewport(0))).toBe(true);
  });

  test('true within the slack', () => {
    expect(isNearTop(viewport(40))).toBe(true);
  });

  test('false in the middle', () => {
    expect(isNearTop(viewport(600))).toBe(false);
  });

  test('a missing element is not near the top', () => {
    // The opposite default from isNearBottom, and deliberately: this one
    // triggers a fetch. Defaulting to true would fire a history load before
    // the transcript has rendered at all.
    expect(isNearTop(null)).toBe(false);
  });
});
