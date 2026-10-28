/**
 * @file_name: mergeTeamMessages.test.ts
 * @description: Folding an incremental poll into what is already on screen.
 *
 * The room re-fetched all 200 messages every 3 seconds. The `since` parameter
 * has existed end to end for a long time — the frontend simply never sent it.
 *
 * What made the full refetch safe was not care, it was `setMessages(all)`:
 * replacing the array wholesale is idempotent by construction, so nothing could
 * duplicate or go stale. Incremental merging has to earn that property back,
 * and these tests are where it is earned.
 *
 * The merge is append-only-with-dedup rather than a general reconciliation
 * because `bus_messages` has no UPDATE path anywhere in the codebase — a
 * message is written once and never edited. A merge that tried to reconcile
 * edits would be solving a problem the data model does not have, while adding
 * one it does not need. If that ever changes, the last test here is the one
 * that should start failing.
 */
import { describe, expect, test } from 'vitest';

import { beforeCursor, mergeTeamMessages, sinceCursor } from '../mergeTeamMessages';

function m(id: string, at: string) {
  return { message_id: id, created_at: at, content: id } as never;
}

describe('mergeTeamMessages', () => {
  test('new messages are appended', () => {
    const out = mergeTeamMessages(
      [m('a', '2026-08-12T09:00:00Z')],
      [m('b', '2026-08-12T09:01:00Z')],
    );
    expect(out.map((x) => x.message_id)).toEqual(['a', 'b']);
  });

  test('a message already on screen is not duplicated', () => {
    // `since` is inclusive on some clocks and the poll overlaps its own window,
    // so the same row WILL come back. Without dedup the room grows a second
    // copy of the last message every three seconds.
    const out = mergeTeamMessages(
      [m('a', '2026-08-12T09:00:00Z'), m('b', '2026-08-12T09:01:00Z')],
      [m('b', '2026-08-12T09:01:00Z'), m('c', '2026-08-12T09:02:00Z')],
    );
    expect(out.map((x) => x.message_id)).toEqual(['a', 'b', 'c']);
  });

  test('an empty poll leaves the transcript untouched', () => {
    const before = [m('a', '2026-08-12T09:00:00Z')];
    const out = mergeTeamMessages(before, []);
    expect(out).toEqual(before);
  });

  test('the same array identity is returned when nothing changed', () => {
    // React re-renders on identity. A poll every three seconds that returns a
    // new array each time would re-render the whole transcript 20 times a
    // minute for nothing — and reset any open expander with it.
    const before = [m('a', '2026-08-12T09:00:00Z')];
    expect(mergeTeamMessages(before, [])).toBe(before);
    expect(mergeTeamMessages(before, [m('a', '2026-08-12T09:00:00Z')])).toBe(before);
  });

  test('order follows time, not arrival', () => {
    // Two agents answering at once can land out of order across a poll
    // boundary; the transcript must still read chronologically.
    const out = mergeTeamMessages(
      [m('a', '2026-08-12T09:00:00Z')],
      [m('c', '2026-08-12T09:03:00Z'), m('b', '2026-08-12T09:01:00Z')],
    );
    expect(out.map((x) => x.message_id)).toEqual(['a', 'b', 'c']);
  });

  test('a first load with no prior state works', () => {
    const out = mergeTeamMessages([], [m('a', '2026-08-12T09:00:00Z')]);
    expect(out.map((x) => x.message_id)).toEqual(['a']);
  });
});

describe('sinceCursor', () => {
  test('is the newest timestamp on screen', () => {
    expect(
      sinceCursor([m('a', '2026-08-12T09:00:00Z'), m('b', '2026-08-12T09:02:00Z')]),
    ).toBe('2026-08-12T09:02:00Z');
  });

  test('is undefined on an empty transcript, so the first poll is a full load', () => {
    // Sending `since=` with nothing to anchor on would ask the server for
    // everything after the epoch, or nothing at all, depending on how it reads
    // an empty string. Not sending it is unambiguous.
    expect(sinceCursor([])).toBeUndefined();
  });

  test('is not confused by an out-of-order tail', () => {
    // The cursor is the WATERMARK, not the last element. Taking the last one
    // after an out-of-order insert would move the cursor backwards and refetch
    // — or worse, forwards past messages never seen.
    expect(
      sinceCursor([m('b', '2026-08-12T09:05:00Z'), m('a', '2026-08-12T09:01:00Z')]),
    ).toBe('2026-08-12T09:05:00Z');
  });

  test('ignores an unparseable timestamp rather than poisoning the cursor', () => {
    // One bad row must not push the cursor to NaN and stall every later poll.
    expect(
      sinceCursor([m('a', 'not-a-date'), m('b', '2026-08-12T09:01:00Z')]),
    ).toBe('2026-08-12T09:01:00Z');
  });
});

describe('beforeCursor', () => {
  test('is the oldest timestamp on screen', () => {
    // The mirror of sinceCursor. Paging back asks for the page above the top of
    // the transcript, so the cursor is the LOW watermark.
    expect(
      beforeCursor([m('b', '2026-08-12T09:02:00Z'), m('a', '2026-08-12T09:00:00Z')]),
    ).toBe('2026-08-12T09:00:00Z');
  });

  test('is undefined on an empty transcript', () => {
    // Nothing on screen means nothing to page above — asking anyway would fetch
    // a second copy of the newest page and merge it into itself.
    expect(beforeCursor([])).toBeUndefined();
  });

  test('ignores an unparseable timestamp', () => {
    expect(
      beforeCursor([m('a', 'not-a-date'), m('b', '2026-08-12T09:01:00Z')]),
    ).toBe('2026-08-12T09:01:00Z');
  });
});

describe('merging an older page', () => {
  test('older messages land above what is on screen', () => {
    // The same merge as the poll: sorted by time, so an older page prepends
    // without a second code path that could disagree with the first.
    const out = mergeTeamMessages(
      [m('c', '2026-08-12T09:02:00Z')],
      [m('a', '2026-08-12T09:00:00Z'), m('b', '2026-08-12T09:01:00Z')],
    );
    expect(out.map((x) => x.message_id)).toEqual(['a', 'b', 'c']);
  });

  test('a page that overlaps what is already there does not duplicate it', () => {
    const out = mergeTeamMessages(
      [m('b', '2026-08-12T09:01:00Z'), m('c', '2026-08-12T09:02:00Z')],
      [m('a', '2026-08-12T09:00:00Z'), m('b', '2026-08-12T09:01:00Z')],
    );
    expect(out.map((x) => x.message_id)).toEqual(['a', 'b', 'c']);
  });
});

// ── the wiring ─────────────────────────────────────────────────────────────
//
// The merge and the cursor are pure and well covered above. What is not covered
// by any of that is whether the panel USES them — and on this branch a feature
// has already been wired to nothing once, surviving with every test green.

describe('the panel polls incrementally and defers scrolling', () => {
  test('the poll sends a cursor and merges the result', async () => {
    const src = await import('../TeamChatPanel.tsx?raw');
    const code = (src as { default: string }).default;

    expect(code).toContain('sinceCursor(messagesRef.current)');
    expect(code).toContain('api.getTeamChat(teamId, cursor)');
    expect(code).toContain('mergeTeamMessages(prev, r.messages)');
  });

  test('a first load without a cursor still replaces wholesale', async () => {
    // Merging into a stale array on a room switch would show the previous
    // team's messages under the new team's name.
    const src = await import('../TeamChatPanel.tsx?raw');
    expect((src as { default: string }).default).toContain(
      'cursor ? mergeTeamMessages(prev, r.messages) : r.messages',
    );
  });

  test('the transcript only auto-scrolls while the reader is at the bottom', async () => {
    const src = await import('../TeamChatPanel.tsx?raw');
    const code = (src as { default: string }).default;

    expect(code).toContain('if (!stickRef.current) return;');
    // And something must actually update it, or the guard is permanently true
    // and the deference does nothing.
    expect(code).toContain('stickRef.current = isNearBottom(');
  });
});
