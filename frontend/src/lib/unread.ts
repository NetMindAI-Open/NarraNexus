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

// ── team rooms ─────────────────────────────────────────────────────────────
//
// A team room is an ASYNC space by design: the user hands it work and leaves.
// Without a mark, leaving is a one-way door — the sidebar row looks identical
// whether six agents have been talking for ten minutes or nothing happened, so
// the only way to find out is to open every room and read.
//
// It is a DOT rather than a count, and that follows from where the data is. The
// sidebar never loads a transcript, so a count would have to come from the
// server — and the server cannot count "unread on this device" without being
// told a per-team watermark it has no way to know. So the split is: the server
// says WHEN the room last said something worth returning for (excluding the
// user's own messages and the platform's own notices — see
// `_team_room_activity`), and this module owns the watermark it is compared to.
//
// The watermark gets its own key space rather than sharing the agent one. Not
// because a collision is known to be possible — it cannot be established from
// the code either way — but because the cost of being wrong is silent: one
// entity would clear the other's mark, and the symptom (a dot that will not
// stay cleared, or clears too early) reads as a bug in the counting.

const TEAM_READ_MARKER_PREFIX = 'teamLastReadMessageTime:';

/** Epoch-ms the user has read this team's room up to (0 = nothing read yet). */
export function getTeamLastReadMs(teamId: string): number {
  try {
    const v = localStorage.getItem(TEAM_READ_MARKER_PREFIX + teamId);
    return v ? new Date(v).getTime() : 0;
  } catch {
    return 0;
  }
}

/**
 * Advance a team's read watermark. Monotonic, for the same reason as the agent
 * one: an out-of-order poll must never un-read what the user already saw.
 */
export function markTeamRead(teamId: string, throughMs: number): void {
  if (!teamId || !throughMs) return;
  try {
    if (throughMs > getTeamLastReadMs(teamId)) {
      localStorage.setItem(
        TEAM_READ_MARKER_PREFIX + teamId,
        new Date(throughMs).toISOString(),
      );
    }
  } catch {
    /* localStorage unavailable — the mark is best-effort */
  }
}

/**
 * The newest `created_at` on screen, as epoch-ms (0 for an empty room).
 *
 * The WATERMARK, not the tail: two agents answering at once can land out of
 * order across a poll boundary, and marking read up to the last element would
 * leave the newer message unread forever.
 *
 * Counts EVERY message including the platform's own lines — the opposite rule
 * from the server's, deliberately. The server decides what is worth a mark; this
 * decides what the user has SEEN, and a line on screen has been seen whoever
 * wrote it. Marking less than what is displayed would leave a room that only
 * narrated itself permanently dotted.
 */
export function latestTeamMessageMs(messages: { created_at?: string | null }[]): number {
  let mx = 0;
  for (const m of messages) {
    // An unparseable row is skipped by the comparison itself: `NaN > mx` is
    // false, so it can never become the maximum. No explicit guard — one was
    // written here first and a mutation proved it changed nothing.
    const t = m.created_at ? Date.parse(m.created_at) : 0;
    if (t > mx) mx = t;
  }
  return mx;
}

/**
 * Does this team's room have something the user has not seen?
 *
 * `lastMessageAt` is the server's answer for this room (null when the room does
 * not exist yet or has said nothing that qualifies) — a room that never spoke is
 * never marked, or every team the user created and never used would carry a dot
 * from the moment it existed.
 */
export function teamHasUnread(
  lastMessageAt: string | null | undefined,
  teamId: string,
): boolean {
  if (!lastMessageAt) return false;
  const at = Date.parse(lastMessageAt);
  // A dot the user cannot clear is worse than a missing one: it teaches them to
  // ignore the dot.
  if (!Number.isFinite(at)) return false;
  return at > getTeamLastReadMs(teamId);
}
