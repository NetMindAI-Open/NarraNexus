/**
 * inboxOrder — microsecond-accurate chronological ordering of inbox messages.
 *
 * Guards the "messages out of order" fix (worst on WeChat): a turn's inbound
 * and reply are stamped 1µs apart server-side and serialised as
 * microsecond-precision ISO strings. Comparing as strings preserves that gap;
 * new Date().getTime() would truncate to ms and leave order to chance.
 */
import { describe, test, expect } from 'vitest';
import { compareInboxMessages } from '@/lib/inboxOrder';

const mk = (created_at: string) => ({ created_at });

describe('compareInboxMessages', () => {
  test('inbound (1µs earlier) sorts before its reply', () => {
    const inbound = mk('2026-07-03T03:29:20.000000+00:00');
    const reply = mk('2026-07-03T03:29:20.000001+00:00');
    const ordered = [reply, inbound].sort(compareInboxMessages);
    expect(ordered).toEqual([inbound, reply]);
  });

  test('two turns read Q1 A1 Q2 A2 (oldest first)', () => {
    const q1 = mk('2026-07-03T03:29:20.000000+00:00');
    const a1 = mk('2026-07-03T03:29:20.000001+00:00');
    const q2 = mk('2026-07-03T03:30:00.000000+00:00');
    const a2 = mk('2026-07-03T03:30:00.000001+00:00');
    const ordered = [a2, q1, a1, q2].sort(compareInboxMessages);
    expect(ordered).toEqual([q1, a1, q2, a2]);
  });

  test('microsecond gap that a millisecond clock would drop is preserved', () => {
    // Both parse to the same millisecond via Date; string compare still orders.
    const earlier = mk('2026-07-03T03:29:20.000000+00:00');
    const later = mk('2026-07-03T03:29:20.000900+00:00');
    expect(compareInboxMessages(earlier, later)).toBeLessThan(0);
    expect(compareInboxMessages(later, earlier)).toBeGreaterThan(0);
  });

  test('equal timestamps compare equal (stable, no flicker)', () => {
    const a = mk('2026-07-03T03:29:20.000000+00:00');
    const b = mk('2026-07-03T03:29:20.000000+00:00');
    expect(compareInboxMessages(a, b)).toBe(0);
  });

  test('missing created_at sorts first without throwing', () => {
    const noTs = mk('');
    const withTs = mk('2026-07-03T03:29:20.000000+00:00');
    const ordered = [withTs, noTs].sort(compareInboxMessages);
    expect(ordered[0]).toBe(noTs);
  });
});
