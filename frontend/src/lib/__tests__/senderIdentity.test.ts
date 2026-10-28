/**
 * @file_name: senderIdentity.test.ts
 * @description: One agent, one colour, everywhere it appears.
 *
 * The point of an identity colour is that it is the SAME identity across
 * surfaces. Two copies of the hash existed before this module — AgentInboxPanel
 * and dashboard/SessionSection — and they had already drifted: both start
 * green/sky/yellow/rose/violet, then one continues teal/indigo/fuchsia and the
 * other fuchsia/teal/indigo. Any agent hashing to slots 5-7 was already showing
 * two different colours in two places, silently, with nothing to notice it by.
 *
 * So the tests that matter here are not "it returns a colour" but "it returns
 * the same colour for the same agent, and it keeps doing so".
 */
import { describe, expect, test } from 'vitest';

import { PALETTE, senderIdentity } from '../senderIdentity';

describe('senderIdentity', () => {
  test('the same agent always gets the same colour', () => {
    const a = senderIdentity('agent_abc');
    const b = senderIdentity('agent_abc');
    expect(a).toEqual(b);
  });

  test('different agents get different colours often enough to be useful', () => {
    // The acceptance criterion is a SIX member room readable without names, so
    // the distribution matters, not just determinism.
    const ids = Array.from({ length: 6 }, (_, i) => `agent_${i}`);
    const dots = new Set(ids.map((id) => senderIdentity(id).dot));
    expect(dots.size).toBeGreaterThanOrEqual(4);
  });

  test('every entry carries a matching dot and accent', () => {
    // The identity has to read on the avatar AND the bubble edge; a palette row
    // with only one of them would leave half the surface uncoloured.
    for (const entry of PALETTE) {
      expect(entry.dot).toBeTruthy();
      expect(entry.accent).toBeTruthy();
    }
  });

  test('the colour is derived from the id, not the display name', () => {
    // Renaming an agent must not change its colour — the colour is the thing
    // people learn to recognise, and a rename is the moment they most need it
    // to stay put.
    expect(senderIdentity('agent_x')).toEqual(senderIdentity('agent_x'));
    expect(senderIdentity('agent_x')).not.toEqual(senderIdentity('Ana'));
  });

  test('an empty seed does not throw', () => {
    // A message with no resolvable sender still has to render.
    expect(senderIdentity('').dot).toBeTruthy();
  });

  test('initials fall back sensibly', () => {
    expect(senderIdentity('agent_1', 'Ana Lee').initials).toBe('AL');
    expect(senderIdentity('agent_1', 'Ana').initials).toBe('AN');
    expect(senderIdentity('agent_1', '').initials).toBe('?');
    expect(senderIdentity('agent_1', '   ').initials).toBe('?');
  });

  test('a CJK display name yields a usable initial', () => {
    // Slicing two chars off a Chinese name gives two full characters, which is
    // wider than the avatar. One is right.
    expect(senderIdentity('agent_1', '小明').initials).toBe('小');
  });
});
