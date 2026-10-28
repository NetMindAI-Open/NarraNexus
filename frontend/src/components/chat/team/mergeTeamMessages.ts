/**
 * @file_name: mergeTeamMessages.ts
 * @author: NarraNexus
 * @date: 2026-08-13
 * @description: Folding an incremental poll into the transcript already on
 * screen.
 *
 * The room re-fetched all 200 messages every 3 seconds. `since` has existed end
 * to end for a long time; the frontend simply never sent it.
 *
 * What made the full refetch correct was not care — it was `setMessages(all)`.
 * Replacing the array wholesale is idempotent by construction, so nothing could
 * duplicate or go stale. Merging incrementally has to earn that property back,
 * which is what this module is for.
 *
 * **Append-only with dedup, not reconciliation.** `bus_messages` has no UPDATE
 * path anywhere in the codebase: a message is written once and never edited. A
 * merge that tried to reconcile edits would solve a problem the data model does
 * not have while adding one it does not need. That assumption is load-bearing,
 * so it is stated here rather than left implicit — if messages ever become
 * mutable, this is the file that has to change first.
 *
 * **Identity is preserved when nothing changed.** React re-renders on identity,
 * and a poll that returned a fresh array every three seconds would re-render
 * the whole transcript twenty times a minute for nothing — resetting any
 * expander the reader had opened along the way.
 */

import type { TeamChatMessage } from '@/types/teams';

/**
 * Merge freshly polled messages into the ones already displayed.
 *
 * Returns the ORIGINAL array reference when the poll added nothing, so a quiet
 * room costs no re-render.
 */
export function mergeTeamMessages(
  current: TeamChatMessage[],
  incoming: TeamChatMessage[],
): TeamChatMessage[] {
  if (!incoming.length) return current;

  const known = new Set(current.map((m) => m.message_id));
  // `since` overlaps its own window on an inclusive clock, so the newest
  // message comes back on every poll. Without this the room grows a duplicate
  // of it every three seconds.
  const fresh = incoming.filter((m) => !known.has(m.message_id));
  if (!fresh.length) return current;

  // Sorted by time, not by arrival: two agents answering at once can land out
  // of order across a poll boundary, and the transcript still has to read
  // chronologically.
  return [...current, ...fresh].sort(
    (a, b) => (Date.parse(a.created_at) || 0) - (Date.parse(b.created_at) || 0),
  );
}

/**
 * The `since` value for the next poll: the newest timestamp on screen.
 *
 * The WATERMARK, not the last element. Taking the tail would move the cursor
 * backwards after an out-of-order insert — refetching — or forwards past
 * messages that were never seen.
 *
 * Undefined for an empty transcript, so the first poll is an unambiguous full
 * load rather than a `since=` the server has to interpret.
 */
/**
 * The `before` value for paging back: the OLDEST timestamp on screen.
 *
 * The mirror of `sinceCursor`, and the two are the room's two directions.
 * Scrolling up asks for the page immediately above the top of the transcript,
 * so this is the low watermark rather than the high one.
 *
 * Undefined for an empty transcript: with nothing on screen there is no page
 * above, and asking anyway would fetch a second copy of the newest page and
 * merge it into itself.
 */
export function beforeCursor(messages: TeamChatMessage[]): string | undefined {
  let bestMs = Infinity;
  let best: string | undefined;
  for (const m of messages) {
    const ms = Date.parse(m.created_at);
    // NaN loses this comparison too, so an unreadable row is skipped without a
    // guard — see the note in sinceCursor.
    if (ms < bestMs) {
      bestMs = ms;
      best = m.created_at;
    }
  }
  return best;
}

export function sinceCursor(messages: TeamChatMessage[]): string | undefined {
  let bestMs = -Infinity;
  let best: string | undefined;
  for (const m of messages) {
    // An unreadable row cannot poison the cursor: `NaN > bestMs` is false, so it
    // never wins the max and `best` keeps the newest row that DID parse. An
    // explicit isFinite guard stood here until a mutation showed it changed
    // nothing.
    const ms = Date.parse(m.created_at);
    if (ms > bestMs) {
      bestMs = ms;
      best = m.created_at;
    }
  }
  return best;
}
