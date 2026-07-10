/**
 * @file_name: inboxOrder.ts
 * @author: Bin Liang
 * @date: 2026-07-03
 * @description: Chronological ordering for inbox messages, microsecond-accurate.
 *
 * A conversational turn's inbound and reply rows are stamped one microsecond
 * apart server-side (channel_inbox_writer). The backend serialises created_at
 * as a microsecond-precision ISO string (inbox route `_to_iso`, "sorts
 * lexicographically in time order"). Comparing those strings preserves the
 * 1µs gap — whereas `new Date(created_at).getTime()` truncates to
 * MILLISECONDS, collapsing a turn's inbound/reply to an equal value and
 * leaving their order to chance (the "reply above its question" bug, worst
 * on WeChat whose messages have no timestamp of their own).
 *
 * Ascending = chat reading order: oldest at top, so a turn reads
 * question-then-answer and turns read Q1 A1 Q2 A2 top-to-bottom.
 */

export interface OrderableMessage {
  created_at?: string | null;
}

/** Compare two inbox messages chronologically (oldest first), microsecond-accurate. */
export function compareInboxMessages(a: OrderableMessage, b: OrderableMessage): number {
  const av = a.created_at || '';
  const bv = b.created_at || '';
  if (av === bv) return 0;
  return av < bv ? -1 : 1;
}
