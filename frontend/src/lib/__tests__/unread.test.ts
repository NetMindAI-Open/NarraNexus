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
import { describe, it, expect, beforeEach } from 'vitest';
import {
  countUnread,
  getLastReadMs,
  markAgentRead,
  latestMessageMs,
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
