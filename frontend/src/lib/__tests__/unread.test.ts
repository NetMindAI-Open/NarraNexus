/**
 * Unit tests for the agent-sidebar unread bookkeeping.
 *
 * Bug (fix/20260521-agent-unread-reappear): opening an agent cleared the
 * unread count only while it stayed the active row — the moment the user
 * switched away the count snapped back, because the "read" marker
 * (lastSeenAwarenessTime) was only ever written when the Awareness tab was
 * opened, never when the chat was read. These tests pin the dedicated
 * read-marker so reading durably zeroes the count.
 */
import { describe, it, test, expect, beforeEach } from 'vitest';
import {
  countUnread,
  getLastReadMs,
  getTeamLastReadMs,
  latestTeamMessageMs,
  markAgentRead,
  markTeamRead,
  latestMessageMs,
  teamHasUnread,
} from '../unread';

beforeEach(() => {
  localStorage.clear();
});

describe('countUnread', () => {
  it('counts only non-user messages newer than the marker', () => {
    const msgs = [
      { role: 'user', timestamp: 100 },
      { role: 'assistant', timestamp: 150 }, // newer than 120 → unread
      { role: 'assistant', timestamp: 110 }, // older than 120 → read
      { role: 'assistant', timestamp: 200 }, // unread
      { role: 'user', timestamp: 300 }, // user's own → never unread
    ];
    expect(countUnread(msgs, 120)).toBe(2);
  });

  it('returns 0 when everything is at or before the marker', () => {
    const msgs = [
      { role: 'assistant', timestamp: 100 },
      { role: 'assistant', timestamp: 120 }, // equal → not strictly newer
    ];
    expect(countUnread(msgs, 120)).toBe(0);
  });

  it('treats a 0 marker as "nothing read yet" (all agent msgs unread)', () => {
    const msgs = [
      { role: 'assistant', timestamp: 1 },
      { role: 'assistant', timestamp: 2 },
    ];
    expect(countUnread(msgs, 0)).toBe(2);
  });
});

describe('markAgentRead / getLastReadMs', () => {
  it('round-trips a read marker', () => {
    expect(getLastReadMs('a1')).toBe(0);
    markAgentRead('a1', 5000);
    expect(getLastReadMs('a1')).toBe(5000);
  });

  it('is monotonic — never moves the marker backwards', () => {
    markAgentRead('a1', 5000);
    markAgentRead('a1', 3000); // older — must be ignored
    expect(getLastReadMs('a1')).toBe(5000);
  });

  it('ignores empty agentId or zero timestamp', () => {
    markAgentRead('', 5000);
    markAgentRead('a1', 0);
    expect(getLastReadMs('a1')).toBe(0);
  });

  it('the read marker zeroes the unread count (the regression)', () => {
    const msgs = [
      { role: 'assistant', timestamp: 1000 },
      { role: 'assistant', timestamp: 2000 },
    ];
    // Before reading: both unread.
    expect(countUnread(msgs, getLastReadMs('a1'))).toBe(2);
    // Reading advances the marker to the latest message...
    markAgentRead('a1', latestMessageMs(msgs));
    // ...so after navigating away the count stays 0.
    expect(countUnread(msgs, getLastReadMs('a1'))).toBe(0);
  });
});

describe('latestMessageMs', () => {
  it('returns the max timestamp, 0 for empty', () => {
    expect(latestMessageMs([])).toBe(0);
    expect(
      latestMessageMs([
        { role: 'assistant', timestamp: 10 },
        { role: 'user', timestamp: 99 },
        { role: 'assistant', timestamp: 50 },
      ]),
    ).toBe(99);
  });
});

// ── team rooms ─────────────────────────────────────────────────────────────
//
// A team room is an ASYNC space by design: the user hands it work and leaves.
// Without an unread badge there is nothing to come back TO — the sidebar looks
// identical whether six agents have been talking for ten minutes or nothing has
// happened at all.
//
// The read marker gets its own key space rather than sharing the agent one. Not
// because a collision is known to be possible — I could not establish from the
// code whether an agent id and a team id can ever be equal — but because the
// cost of being wrong is silent: one entity would clear the other's badge, and
// the symptom (a badge that will not stay cleared, or clears too early) looks
// like a bug in the counting rather than in the key.

// The mark is a DOT, not a count, and that follows from where the data is. The
// sidebar never loads a transcript, so the count would have to come from the
// server — and the server cannot count "unread on this device" without being
// told a per-team watermark it does not have. Which messages qualify is decided
// server-side (`_team_room_activity`: the user's own messages and the platform's
// own notices do not); this module owns the other half, the watermark itself.

describe('team read markers', () => {
  beforeEach(() => localStorage.clear());

  test('a team marker is independent of an agent with the same id', () => {
    // The property that makes a separate key space worth having. Not because a
    // collision is known to be possible — it cannot be established from the code
    // either way — but because being wrong is silent: one entity would clear the
    // other's mark, and the symptom looks like a bug in the counting.
    markTeamRead('x', 5000);

    expect(getTeamLastReadMs('x')).toBe(5000);
    expect(getLastReadMs('x')).toBe(0);
  });

  test('and an agent marker is independent of a team with the same id', () => {
    markAgentRead('x', 5000);

    expect(getLastReadMs('x')).toBe(5000);
    expect(getTeamLastReadMs('x')).toBe(0);
  });

  test('the marker is monotonic', () => {
    // A poll that returns an older tail must never un-read what the user saw.
    markTeamRead('t1', 5000);
    markTeamRead('t1', 3000);
    expect(getTeamLastReadMs('t1')).toBe(5000);
  });

  test('nothing read yet reads as zero', () => {
    expect(getTeamLastReadMs('never-opened')).toBe(0);
  });

  test('an empty id or a zero timestamp is a no-op', () => {
    markTeamRead('', 5000);
    markTeamRead('t2', 0);
    expect(getTeamLastReadMs('t2')).toBe(0);
  });
});

describe('latestTeamMessageMs', () => {
  test('is the newest created_at, not the last element', () => {
    // Two agents answering at once can land out of order across a poll boundary.
    // Marking read up to the TAIL would leave the newer message unread forever.
    expect(
      latestTeamMessageMs([
        { created_at: '2026-08-13T09:05:00Z' },
        { created_at: '2026-08-13T09:01:00Z' },
      ]),
    ).toBe(Date.parse('2026-08-13T09:05:00Z'));
  });

  test('an empty room is zero, so opening it marks nothing', () => {
    expect(latestTeamMessageMs([])).toBe(0);
  });

  test('an unparseable timestamp is skipped rather than poisoning the mark', () => {
    // NaN would compare false against everything and silently stop the marker
    // from ever advancing again.
    expect(
      latestTeamMessageMs([{ created_at: 'nonsense' }, { created_at: '2026-08-13T09:01:00Z' }]),
    ).toBe(Date.parse('2026-08-13T09:01:00Z'));
  });

  test('everything counts, including the platform lines', () => {
    // The opposite rule from the server's, and deliberately: the server decides
    // what is WORTH a mark, this decides what the user has SEEN — and a line on
    // screen has been seen whoever wrote it. Marking less than what is on screen
    // would leave a room that only narrated itself permanently dotted.
    expect(
      latestTeamMessageMs([
        { created_at: '2026-08-13T09:01:00Z' },
        { created_at: '2026-08-13T09:09:00Z' },
      ]),
    ).toBe(Date.parse('2026-08-13T09:09:00Z'));
  });
});

describe('teamHasUnread', () => {
  test('a room that has never spoken is not marked', () => {
    // Every team the user created and never used would otherwise carry a dot
    // from the moment it existed.
    expect(teamHasUnread(null, 't1')).toBe(false);
    expect(teamHasUnread(undefined, 't1')).toBe(false);
  });

  test('a room that spoke after the last read is marked', () => {
    markTeamRead('t1', Date.parse('2026-08-13T09:00:00Z'));
    expect(teamHasUnread('2026-08-13T09:05:00Z', 't1')).toBe(true);
  });

  test('a room the user has caught up on is not marked', () => {
    markTeamRead('t1', Date.parse('2026-08-13T09:05:00Z'));
    expect(teamHasUnread('2026-08-13T09:05:00Z', 't1')).toBe(false);
  });

  test('a room never opened but already talking is marked', () => {
    // Marker 0 must mean "nothing read", not "everything read" — an agent that
    // answered before the user ever opened the room is exactly the case the dot
    // exists for.
    expect(teamHasUnread('2026-08-13T09:05:00Z', 'never-opened')).toBe(true);
  });

  test('an unparseable server timestamp does not mark the room', () => {
    // A dot the user cannot clear (nothing to compare against) is worse than a
    // missing one: it trains them to ignore the dot.
    expect(teamHasUnread('nonsense', 't1')).toBe(false);
  });
});
