/**
 * Unread-message bookkeeping for the agent sidebar.
 *
 * Why this exists
 * ---------------
 * The sidebar shows a per-agent "unread" count pill. It used to be computed
 * against `lastSeenAwarenessTime:<aid>` — a localStorage marker written ONLY
 * when the user opened the Awareness tab, never when they read the chat. So
 * the count zeroed only via a render-time special case (the active row), and
 * the instant the user navigated to another agent it snapped back: the
 * "read" marker had never advanced past the messages they'd just seen.
 *
 * This module gives reading its own durable, monotonic marker
 * (`lastReadMessageTime:<aid>`), decoupled from the Awareness indicator.
 */

const READ_MARKER_PREFIX = 'lastReadMessageTime:';

export interface UnreadMessage {
  role: string;
  timestamp?: number;
}

/** Epoch-ms the user has read this agent up to (0 = nothing read yet). */
export function getLastReadMs(agentId: string): number {
  try {
    const v = localStorage.getItem(READ_MARKER_PREFIX + agentId);
    return v ? new Date(v).getTime() : 0;
  } catch {
    return 0;
  }
}

/**
 * Advance the read marker to `throughMs`. Monotonic — a late-arriving older
 * timestamp must never "un-read" messages the user already saw. No-op for an
 * empty agentId or a zero timestamp.
 */
export function markAgentRead(agentId: string, throughMs: number): void {
  if (!agentId || !throughMs) return;
  try {
    if (throughMs > getLastReadMs(agentId)) {
      localStorage.setItem(
        READ_MARKER_PREFIX + agentId,
        new Date(throughMs).toISOString(),
      );
    }
  } catch {
    /* localStorage unavailable — unread tracking is best-effort */
  }
}

/** Count agent (non-user) messages strictly newer than the read marker. */
export function countUnread(messages: UnreadMessage[], lastReadMs: number): number {
  let n = 0;
  for (const m of messages) {
    if (m.role !== 'user' && (m.timestamp ?? 0) > lastReadMs) n += 1;
  }
  return n;
}

/** Latest timestamp across messages (0 when empty). */
export function latestMessageMs(messages: UnreadMessage[]): number {
  let mx = 0;
  for (const m of messages) {
    const t = m.timestamp ?? 0;
    if (t > mx) mx = t;
  }
  return mx;
}
