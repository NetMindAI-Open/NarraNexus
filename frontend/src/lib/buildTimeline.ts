/**
 * @file_name: buildTimeline.ts
 * @description: Pure builder for ChatPanel's unified conversation timeline.
 *
 * The chat view merges two independently-produced sources into one
 * chronological list:
 *   - history  — the chat transcript persisted in
 *                `instance_json_format_memory_chat` (ChatModule's per-instance
 *                JSON memory), fetched via getSimpleChatHistory. NOT the
 *                `events` table, and NOT `agent_messages` (a tombstone table
 *                with no writer — reading "0 rows" there and concluding the
 *                panel replays `events` is how the 0802 ordering report got
 *                misdiagnosed).
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
 * them (session = owner-facing tool args joined by `\n\n`;
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
import { isBlankText } from './isBlankText';
import { NO_RESPONSE_MARKER } from './turnMarkers';

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
  // Self-serviceable failure reason (config_actionable). Carried through the
  // same session→timeline hop as isError so MessageBubble can render "what you
  // can do" guidance. Dropping it here is what made the live actionable panel
  // silently fall back to the generic "Run failed" popover.
  actionReason?: string;
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
    actionReason: msg.actionReason,
  };
}

/**
 * Merge persisted history + live session messages into one chronological,
 * de-duplicated timeline.
 *
 * @param historyMessages rows from getSimpleChatHistory (oldest → newest)
 * @param sessionMessages chatStore session messages (oldest → newest)
 */
/** The session id ChatPanel gives the first-run greeting it folds into the
 *  session on the user's first send. The backend seeds the same greeting into
 *  history during that turn, so once history carries a `bootstrap` row the
 *  folded copy must go — by identity, not by text: the client localises its
 *  copy while the persisted row keeps the backend's text, so the content
 *  heuristic below never matched and the greeting showed twice until a reload. */
export const BOOTSTRAP_GREETING_SESSION_ID = 'bootstrap-greeting';

export function buildUnifiedTimeline(
  historyMessages: SimpleChatMessage[],
  sessionMessages: ChatMessage[],
): TimelineItem[] {
  const items: TimelineItem[] = [];
  const historyHasBootstrapGreeting = historyMessages.some((m) => m.bootstrap === true);

  // ── 1. History messages (from DB) ──────────────────────────────────
  for (let i = 0; i < historyMessages.length; i++) {
    const msg = historyMessages[i];

    // Filter out legacy junk: a non-chat working source that persisted the
    // "no response needed" sentinel as if it were a real message.
    const isNonChat = msg.working_source && msg.working_source !== 'chat';
    if (isNonChat && msg.content === NO_RESPONSE_MARKER) continue;

    // Blank-bubble guard: rows already persisted with whitespace-only
    // content (pre-strip-guard history) render as empty bubbles. Skip
    // them — unless they carry attachments, which render on their own.
    // NOTE: a skipped row also stays OUT of the dedup indexes below
    // (historyEventRoleKeys / historyByContentKey), so a session copy
    // sharing its event_id would render. Harmless today — the backend
    // guards stop producing blank rows, and stale blank rows have no
    // live session — but keep it in mind if a blank producer returns.
    if (isBlankText(msg.content) && !msg.attachments?.length) continue;

    // NOTE: message_bus activity markers are intentionally NOT dropped here.
    // Keeping the owner's 1:1 chat clean of them is now handled upstream — the
    // conversation tab fetches `include=chat` (backend drops activity rows) and
    // ChatPanel's `visibleTimeline` filters `messageType === 'activity'` out of
    // it — so this loop must PASS these rows through for the Activity Log
    // (inner) tab to render peer/team activity at all.
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
    if (msg.id === BOOTSTRAP_GREETING_SESSION_ID && historyHasBootstrapGreeting) continue;
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
