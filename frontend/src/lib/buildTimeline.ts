/**
 * @file_name: buildTimeline.ts
 * @description: Pure builder for ChatPanel's unified conversation timeline.
 *
 * The chat view merges two independently-produced sources into one
 * chronological list:
 *   - history  — rows persisted in `agent_messages`, fetched via
 *                getSimpleChatHistory
 *   - session  — messages held live in chatStore (the user prompt added
 *                on send, the assistant reply assembled at stopStreaming)
 *
 * Every turn is in BOTH sources once it finishes (history has the
 * persisted copy, session still holds the live copy). The builder must
 * drop the session copy so each turn renders exactly once.
 *
 * ## Dedup — event_id first, content heuristic only as fallback
 *
 * Primary key: `(role, event_id)`. Every message of a turn — the user
 * prompt and the assistant reply — is persisted with that turn's
 * `event_id`, and chatStore stamps the SAME `event_id` onto the session
 * copies. `${role}:${event_id}` is therefore an exact, formatting-immune
 * identity.
 *
 * This replaces a `${role}:${content}` exact-string key that silently
 * missed whenever the session-assembled content and the DB-persisted
 * content drifted by even one character — different code paths produce
 * them (session = `send_message_to_user_directly` args joined by `\n\n`;
 * history = whatever the backend persisted, which it sometimes rewrites,
 * e.g. owner-notify substitution). That drift made the latest reply
 * occasionally render twice. event_id is immune to it.
 *
 * Fallback: `(role, content)` + a 5-min timestamp window + match-and-
 * consume. Used ONLY for messages with no `event_id` — legacy history
 * rows, or a session message created before `run_started` landed. The
 * consume step keeps the Bug-19 retry semantics (user sends the exact
 * same text twice — each session copy pairs one-to-one with a history
 * row, the retry is not swallowed).
 *
 * Pure & dependency-free so it can be unit-tested directly — see
 * lib/__tests__/buildTimeline.test.ts.
 */

import type { SimpleChatMessage } from '@/types/api';
import type { ChatMessage, AgentToolCall, Attachment, TurnEvent } from '@/types';

/** Unified message item for the single timeline. */
export interface TimelineItem {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
  source: 'history' | 'session';  // Where this message came from (for dedup/debug)
  messageType?: string;           // "activity" for background activity records
  workingSource?: string;         // "chat" | "job" | "lark"
  eventId?: string;               // Associated Event ID
  thinking?: string;              // Reasoning content (from session messages)
  toolCalls?: AgentToolCall[];    // Tool calls (from session messages)
  attachments?: Attachment[];     // User-uploaded files referenced by this message
  timeline?: TurnEvent[];         // Live-stream timeline carried over (just-finished turn)
  // Error state carried through so MessageBubble can surface it. Dropping
  // these two on the session→timeline hop is exactly why the red error
  // bubble + warning list silently stopped rendering after the May-2026
  // unified-timeline refactor — a real error looked like a normal "no
  // reply" message. isError = the turn failed (content IS the error text);
  // warnings = non-fatal errors that occurred alongside a real reply.
  isError?: boolean;
  warnings?: string[];
}

/** Match window for the event-id-less content fallback. Generous because
 *  it only needs to cover wall-clock skew between the browser and the
 *  server, not real message spacing — see ChatPanel history-dedup notes. */
const SAME_MESSAGE_WINDOW_MS = 300_000;

function toSessionItem(msg: ChatMessage): TimelineItem {
  return {
    id: msg.id,
    role: msg.role,
    content: msg.content,
    timestamp: msg.timestamp,
    source: 'session',
    eventId: msg.event_id,
    thinking: msg.thinking,
    toolCalls: msg.toolCalls,
    attachments: msg.attachments,
    timeline: msg.timeline,
    isError: msg.isError,
    warnings: msg.warnings,
  };
}

/**
 * Merge persisted history + live session messages into one chronological,
 * de-duplicated timeline.
 *
 * @param historyMessages rows from getSimpleChatHistory (oldest → newest)
 * @param sessionMessages chatStore session messages (oldest → newest)
 */
export function buildUnifiedTimeline(
  historyMessages: SimpleChatMessage[],
  sessionMessages: ChatMessage[],
): TimelineItem[] {
  const items: TimelineItem[] = [];

  // ── 1. History messages (from DB) ──────────────────────────────────
  for (let i = 0; i < historyMessages.length; i++) {
    const msg = historyMessages[i];

    // Filter out legacy junk: a non-chat working source that persisted the
    // "no response needed" sentinel as if it were a real message.
    const isNonChat = msg.working_source && msg.working_source !== 'chat';
    if (isNonChat && msg.content === '(Agent decided no response needed)') continue;

    // Hide message-bus background-activity markers from the agent's 1:1 chat.
    // A team group-chat turn (the agent was @mentioned) lives in the team room,
    // not here — surfacing it as "Background activity (message_bus)" just
    // confuses the owner looking at their direct conversation.
    if (msg.message_type === 'activity' && msg.working_source === 'message_bus') continue;

    items.push({
      id: `h-${i}`,
      role: msg.role,
      content: msg.content,
      timestamp: msg.timestamp ? new Date(msg.timestamp).getTime() : 0,
      source: 'history',
      messageType: msg.message_type,
      workingSource: msg.working_source,
      eventId: msg.event_id,
      attachments: msg.attachments,
    });
  }

  // ── 2. Index history for dedup ─────────────────────────────────────
  // `items` currently holds ONLY history rows.
  const historyEventRoleKeys = new Set<string>();              // `${role}:${event_id}`
  const historyByContentKey = new Map<string, number[]>();     // `${role}:${content}` → timestamps
  for (const item of items) {
    if (item.eventId) {
      historyEventRoleKeys.add(`${item.role}:${item.eventId}`);
    }
    const ck = `${item.role}:${item.content}`;
    const list = historyByContentKey.get(ck);
    if (list) list.push(item.timestamp);
    else historyByContentKey.set(ck, [item.timestamp]);
  }

  // ── 3. Session messages — dedup against history, then add ──────────
  for (const msg of sessionMessages) {
    // Primary path: exact (role, event_id) identity.
    if (msg.event_id) {
      if (historyEventRoleKeys.has(`${msg.role}:${msg.event_id}`)) {
        // This exact (role, turn) is already persisted — drop the session copy.
        continue;
      }
      // Has an event_id but history doesn't carry this (role, turn) yet
      // (turn just finished, history hasn't reloaded). Render the session
      // copy. Do NOT fall through to the content heuristic — event_id is
      // authoritative, and the heuristic could false-positive against an
      // unrelated row with the same text.
      items.push(toSessionItem(msg));
      continue;
    }

    // Fallback path: event-id-less message (legacy, or created before
    // `run_started` landed). Match by (role, content) within a time window
    // and CONSUME the matched history timestamp so a second session
    // message of the same role+content (a user retry) can't re-pair with
    // the same history row.
    const ck = `${msg.role}:${msg.content}`;
    const historyTimestamps = historyByContentKey.get(ck);
    const matchIdx = historyTimestamps
      ? historyTimestamps.findIndex(
          (ts) => Math.abs(msg.timestamp - ts) < SAME_MESSAGE_WINDOW_MS,
        )
      : -1;
    if (matchIdx >= 0 && historyTimestamps) {
      historyTimestamps.splice(matchIdx, 1);
      continue;
    }

    items.push(toSessionItem(msg));
  }

  // ── 4. Sort by timestamp, id as a stable tie-breaker ───────────────
  items.sort((a, b) => {
    if (a.timestamp !== b.timestamp) return a.timestamp - b.timestamp;
    return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
  });

  return items;
}
