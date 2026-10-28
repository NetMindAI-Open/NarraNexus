/**
 * Chat store — multi-agent concurrent session management
 *
 * Core design: agentSessions map indexed by agentId, each agent has independent chat state.
 * Flat top-level fields (messages, isStreaming, etc.) are auto-derived from activeAgentId's
 * session after every set() call, preserving backward compatibility for consumers.
 */

import { create } from 'zustand';
import { useBookmarkStore } from './bookmarkStore';
import type {
  ChatMessage,
  Step,
  ConversationRound,
  RuntimeMessage,
  ProgressMessage,
  AgentTextDelta,
  AgentThinking,
  AgentReplyDelta,
  AgentPlanMessage,
  AgentToolCall,
  ErrorMessage,
  TurnEvent,
} from '@/types';
import { generateId } from '@/lib/utils';
import { INTERRUPTED_MARKER, NO_RESPONSE_MARKER } from '@/lib/turnMarkers';
import { isBlankText } from '@/lib/isBlankText';
import { notifyAgentReplyCompleted } from '@/lib/desktopNotify';
import { segmentTurn } from '@/lib/segmentTurn';
import { captureProductEvent } from '@/lib/productAnalytics';
import { isOwnerReplyTool } from '@/lib/ownerTools';
import { isMonologueFrame } from '@/lib/monologueTier';

// Pipeline step count is determined dynamically from the steps received
// during streaming. No hardcoded total — adapts to backend changes.

/** Per-agent independent chat state */
export interface AgentChatState {
  messages: ChatMessage[];
  currentSteps: Step[];
  currentThinking: string;
  currentToolCalls: AgentToolCall[];
  currentErrors: string[];
  /** Set when a config_actionable (deterministic self-serviceable) error
   *  arrives this turn — carries the reason so stopStreaming can stamp it
   *  onto the assistant message for actionable UI. Cleared on startStreaming. */
  currentActionReason: string | null;
  currentAssistantMessage: string;
  isStreaming: boolean;
  history: ConversationRound[];

  /** Inline timeline events for the *currently streaming* turn — append-only,
   *  cleared on startStreaming. See TurnEvent for the per-event contract.
   *  Once the turn settles, the snapshot is attached to the assistant
   *  ChatMessage as `message.timeline` (so MessageBubble owns rendering of
   *  the completed turn under "View reasoning & tools"). No separate
   *  "last turn" buffer is kept — the just-finished turn is just a normal
   *  history bubble that happens to have a `timeline`. */
  currentEvents: TurnEvent[];

  /** The run/event id of the current (or just-finished) turn — set from
   *  the `run_started` frame (fresh run) or `setCurrentRunId` (reconnect),
   *  reset to null on startStreaming. Stamped onto the user prompt and the
   *  assistant reply so the unified timeline can dedup session copies
   *  against persisted history rows by exact (role, event_id) — see
   *  lib/buildTimeline.ts. */
  currentRunId: string | null;

  /** Set when the current streaming turn is a RECONNECT to an already-running
   *  backend run (Phase C), not a fresh generation — carries the run's real
   *  start time so the UI can say "resumed · running for N min" instead of
   *  looking like a from-scratch re-generation after a page refresh
   *  (Shenzhen-r2 B1). Cleared on startStreaming; rendered only while
   *  isStreaming, so it never needs an explicit clear at settle. */
  resumedRun: { runId: string; startedAtMs: number } | null;
  /** Whether the CURRENT run can accept a mid-run steer (from
   *  `run_started.steerable`). Gates SENDING during a run (the composer stays
   *  editable either way): a steerable run folds a follow-up into the same turn,
   *  a non-steerable one holds the draft until the run ends. Also drives the
   *  steer send-button + placeholder. Reset false on startStreaming, set on
   *  run_started. */
  currentSteerable: boolean;
}

/**
 * Toast notification for something that finished while the user was elsewhere.
 *
 * A discriminated union rather than an agent item with an optional team id: a
 * team toast has no agent to switch to and no agent id to be keyed by, and one
 * field carrying two meanings is the shape that makes the eventual bug
 * invisible. The kind decides both the wording and where "View" goes.
 */
export type ToastItem =
  | { kind: 'agent'; agentId: string; agentName: string; timestamp: number }
  | { kind: 'team'; teamId: string; teamName: string; timestamp: number }
  /**
   * A heal repointed an artifact at a different file (artifact_changed
   * action="repointed"). The pointer moved without the user asking — honesty
   * requires telling them where it went and whether the content hash
   * verified the candidate (spec artifact-events §5.3).
   */
  | {
      kind: 'artifact-repointed';
      artifactId: string;
      title: string;
      oldPath: string;
      newPath: string;
      hashMatched: boolean;
      timestamp: number;
    };

/**
 * The queue key for a toast — `agent:<id>` / `team:<id>`.
 *
 * Kind-qualified so a team and an agent that happen to share an id cannot
 * dismiss one another. Whether that can happen could not be established from
 * the code, which is the reason to make it impossible rather than to rely on it.
 */
export function toastKey(t: ToastItem): string {
  if (t.kind === 'agent') return `agent:${t.agentId}`;
  if (t.kind === 'team') return `team:${t.teamId}`;
  return `artifact:${t.artifactId}`;
}

/** Shared frozen default — avoids creating new objects on every access for non-existent sessions */
const DEFAULT_AGENT_STATE: AgentChatState = Object.freeze({
  messages: Object.freeze([]) as unknown as ChatMessage[],
  currentSteps: Object.freeze([]) as unknown as Step[],
  currentThinking: '',
  currentToolCalls: Object.freeze([]) as unknown as AgentToolCall[],
  currentErrors: Object.freeze([]) as unknown as string[],
  currentActionReason: null,
  currentAssistantMessage: '',
  isStreaming: false,
  history: Object.freeze([]) as unknown as ConversationRound[],
  currentEvents: Object.freeze([]) as unknown as TurnEvent[],
  currentRunId: null,
  resumedRun: null,
  currentSteerable: false,
});

/** Create a fresh mutable state for a new agent session */
function createDefaultAgentState(): AgentChatState {
  return {
    messages: [],
    currentSteps: [],
    currentThinking: '',
    currentToolCalls: [],
    currentErrors: [],
    currentActionReason: null,
    currentAssistantMessage: '',
    isStreaming: false,
    history: [],
    currentEvents: [],
    currentRunId: null,
    resumedRun: null,
    currentSteerable: false,
  };
}

interface ChatState {
  // Multi-agent session map
  agentSessions: Record<string, AgentChatState>;
  activeAgentId: string;

  // Bumped to force ChatPanel to re-fetch server-side history (e.g. after a
  // data wipe clears conversations — the panel holds its own loaded history
  // and would otherwise keep showing stale messages until an agent switch).
  historyRefreshTick: number;

  // The same idea for the team workspace panel. It is a SEPARATE tick rather
  // than a reuse of the one above because the two wipes are independently
  // selectable: clearing only a team's files leaves the transcript untouched,
  // and the workspace loader keys on message count, so nothing about the chat
  // changes to tell it the files are gone. Firing the history tick for a
  // file-only wipe would also make every ChatPanel refetch history it knows is
  // current.
  workspaceRefreshTick: number;

  // Notification state
  completedAgentIds: string[];
  toastQueue: ToastItem[];

  // Derived flat fields (auto-synced from active agent's session after every set())
  messages: ChatMessage[];
  currentSteps: Step[];
  currentThinking: string;
  currentToolCalls: AgentToolCall[];
  currentErrors: string[];
  currentAssistantMessage: string;
  isStreaming: boolean;
  history: ConversationRound[];
  currentEvents: TurnEvent[];
  resumedRun: AgentChatState['resumedRun'];
  currentRunId: string | null;
  currentSteerable: boolean;

  // Actions (all accept agentId)
  setActiveAgent: (agentId: string) => void;
  addUserMessage: (
    agentId: string,
    content: string,
    attachments?: import('@/types').Attachment[],
    timestampMs?: number,
  ) => string;
  /** Optimistic mid-run steer bubble (owner follow-up during a live run). */
  addSteerMessage: (agentId: string, content: string, clientMsgId: string) => string;
  /** Mark a steer bubble rejected locally (by clientMsgId) when the send never
   *  left the client, so it doesn't hang 'queued' with no backend ack coming. */
  markSteerRejected: (agentId: string, clientMsgId: string, reason: string) => void;
  startStreaming: (agentId: string) => void;
  stopStreaming: (agentId: string, agentName?: string, opts?: { cancelled?: boolean }) => void;
  /** Set the current run's event_id and backfill it onto the trailing
   *  event-id-less user message. Called from the `run_started` frame
   *  (fresh run) and from wsManager on reconnect. */
  setCurrentRunId: (agentId: string, runId: string) => void;
  markResumedRun: (agentId: string, runId: string, startedAtMs: number) => void;
  processMessage: (agentId: string, message: RuntimeMessage) => void;
  clearAgent: (agentId: string) => void;
  clearAll: () => void;
  /** Force any mounted ChatPanel to reload its server history. */
  requestHistoryRefresh: () => void;
  /** Force any mounted team workspace panel to reload artifacts and files. */
  requestWorkspaceRefresh: () => void;

  // Notification actions
  /**
   * Append one toast (deduped by toastKey — a second push for the same key
   * replaces the first so its auto-dismiss timer restarts). Used by the
   * artifact event dispatcher; agent/team completion toasts keep their
   * dedicated enqueue paths.
   */
  pushToast: (item: ToastItem) => void;
  /** Remove one toast by its `toastKey` (`agent:<id>` / `team:<id>` / `artifact:<id>`). */
  dismissToast: (key: string) => void;
  clearCompletedNotification: (agentId: string) => void;

  // Query helpers
  isAgentStreaming: (agentId: string) => boolean;
  runningAgentIds: () => string[];
}

/** Get agent session, returning shared frozen default for non-existent sessions */
function getSession(sessions: Record<string, AgentChatState>, agentId: string): AgentChatState {
  return sessions[agentId] ?? DEFAULT_AGENT_STATE;
}

/** Update a specific agent's session immutably */
function updateSession(
  sessions: Record<string, AgentChatState>,
  agentId: string,
  updater: (session: AgentChatState) => Partial<AgentChatState>,
): Record<string, AgentChatState> {
  const current = sessions[agentId] ?? createDefaultAgentState();
  return {
    ...sessions,
    [agentId]: { ...current, ...updater(current) },
  };
}

/**
 * The single place that "recognises a steer bubble and re-stamps its status"
 * lives. Every steer transition (local reject, steer_consumed→merged,
 * steer_rejected→rejected, run-end sweep) flows through here so the match
 * guard and the patch shape can't drift into subtly different copies.
 *
 * A message with no `steerClientMsgId` is NEVER a steer bubble, so it is always
 * left untouched — this guard is what stops a stray `steer_rejected` whose
 * `client_msg_id` is undefined from matching every ordinary (undefined-id)
 * bubble on screen and flipping the whole timeline to 'rejected'. `match`
 * receives the (always-defined) client id plus the full message so a caller can
 * key on either (e.g. the run-end sweep keys on `steerStatus === 'queued'`).
 */
function patchSteerBubbles(
  messages: ChatMessage[],
  match: (clientMsgId: string, m: ChatMessage) => boolean,
  patch: (m: ChatMessage) => Partial<ChatMessage>,
): ChatMessage[] {
  return messages.map((m) =>
    m.steerClientMsgId && match(m.steerClientMsgId, m) ? { ...m, ...patch(m) } : m,
  );
}

/** Derive flat fields from the active agent's session */
function deriveFlatFields(state: { agentSessions: Record<string, AgentChatState>; activeAgentId: string }) {
  const session = getSession(state.agentSessions, state.activeAgentId);
  return {
    messages: session.messages,
    currentSteps: session.currentSteps,
    currentThinking: session.currentThinking,
    currentToolCalls: session.currentToolCalls,
    currentErrors: session.currentErrors,
    currentAssistantMessage: session.currentAssistantMessage,
    isStreaming: session.isStreaming,
    history: session.history,
    currentEvents: session.currentEvents,
    resumedRun: session.resumedRun,
    currentRunId: session.currentRunId,
    currentSteerable: session.currentSteerable,
  };
}

export const useChatStore = create<ChatState>((_set, get) => {
  /**
   * Wrapped set: after every state update, auto-derive flat fields from the active session.
   * This ensures consumers reading `messages`, `isStreaming`, etc. always get correct values
   * without needing to know about the session map.
   */
  const set: typeof _set = (partial) => {
    _set((prevState) => {
      const partialResult = typeof partial === 'function' ? partial(prevState) : partial;
      const merged = { ...prevState, ...partialResult };
      return {
        ...partialResult,
        ...deriveFlatFields(merged),
      };
    });
  };

  return {
    // Multi-agent state
    agentSessions: {},
    activeAgentId: '',
    historyRefreshTick: 0,
    workspaceRefreshTick: 0,
    completedAgentIds: [],
    toastQueue: [],

    // Initial flat fields (derived from empty active session)
    ...deriveFlatFields({ agentSessions: {}, activeAgentId: '' }),

    // Switch active agent (also clears its completion notification)
    setActiveAgent: (agentId: string) => {
      set((state) => ({
        activeAgentId: agentId,
        completedAgentIds: state.completedAgentIds.filter((id) => id !== agentId),
      }));
    },

    // Add user message to a specific agent's session.
    //
    // ``timestampMs`` is optional and only used by the reconnect path:
    // when the frontend joins a run that was started in a previous tab
    // session, the server hands us ``events.created_at`` (same value
    // ChatModule will later write into agent_messages.user_ts). Using
    // that exact ms keeps ChatPanel's role:content + 60s dedup honest
    // — the injected bubble and the eventual history row collapse
    // into one timeline item. Fresh-run callers omit it so the
    // user-pressed-Enter moment is captured locally as Date.now().
    addUserMessage: (
      agentId: string,
      content: string,
      attachments?: import('@/types').Attachment[],
      timestampMs?: number,
    ) => {
      const id = generateId();
      const message: ChatMessage = {
        id,
        role: 'user',
        content,
        timestamp: typeof timestampMs === 'number' && Number.isFinite(timestampMs) ? timestampMs : Date.now(),
        ...(attachments && attachments.length > 0 ? { attachments } : {}),
      };
      set((state) => ({
        agentSessions: updateSession(state.agentSessions, agentId, (s) => ({
          messages: [...s.messages, message],
        })),
      }));
      return id;
    },

    // Add an optimistic mid-run steer bubble (owner follow-up sent WHILE a run
    // streams). Starts as 'queued'; processMessage flips it to 'merged'
    // (steer_consumed) or 'rejected' (steer_rejected) by steerClientMsgId.
    addSteerMessage: (agentId: string, content: string, clientMsgId: string) => {
      const id = generateId();
      const message: ChatMessage = {
        id,
        role: 'user',
        content,
        timestamp: Date.now(),
        steerStatus: 'queued',
        steerClientMsgId: clientMsgId,
      };
      set((state) => ({
        agentSessions: updateSession(state.agentSessions, agentId, (s) => ({
          messages: [...s.messages, message],
        })),
      }));
      return id;
    },

    // Mark a steer bubble rejected locally (by clientMsgId) — used when the send
    // never left the client (steer() returned false: the socket was no longer
    // steerable), so the bubble doesn't hang 'queued' with no backend ack coming.
    markSteerRejected: (agentId: string, clientMsgId: string, reason: string) => {
      set((state) => ({
        agentSessions: updateSession(state.agentSessions, agentId, (s) => ({
          messages: patchSteerBubbles(
            s.messages,
            (id) => id === clientMsgId,
            () => ({ steerStatus: 'rejected', rejectReason: reason }),
          ),
        })),
      }));
    },

    // Start streaming for a specific agent
    startStreaming: (agentId: string) => {
      set((state) => ({
        agentSessions: updateSession(state.agentSessions, agentId, () => ({
          isStreaming: true,
          currentAssistantMessage: '',
          currentSteps: [],
          currentThinking: '',
          currentToolCalls: [],
          currentErrors: [],
          currentActionReason: null,
          currentEvents: [],
          // Clean slate — the run id of this new turn arrives via the
          // `run_started` frame (fresh) or setCurrentRunId (reconnect).
          currentRunId: null,
          // A fresh turn is a fresh generation until a run_reconnect frame
          // says otherwise (markResumedRun fires AFTER this reset on the
          // reconnect path — onopen → startStreaming → run_reconnect).
          resumedRun: null,
          // Steerability is (re)declared by run_started; assume not until then.
          currentSteerable: false,
        })),
      }));
    },

    // Set the current run's event_id and backfill it onto the trailing
    // event-id-less user message (the prompt that triggered this run), so
    // both the user message and the eventual assistant reply carry the
    // same event_id for exact (role, event_id) timeline dedup.
    setCurrentRunId: (agentId: string, runId: string) => {
      set((state) => ({
        agentSessions: updateSession(state.agentSessions, agentId, (s) => {
          const lastIdx = s.messages.length - 1;
          const last = lastIdx >= 0 ? s.messages[lastIdx] : undefined;
          const messages =
            last && last.role === 'user' && !last.event_id
              ? s.messages.map((m, i) =>
                  i === lastIdx ? { ...m, event_id: runId } : m,
                )
              : s.messages;
          return { currentRunId: runId, messages };
        }),
      }));
    },

    // Mark the streaming turn as a reconnect to an already-running run
    // (run_reconnect frame), anchored to the run's REAL start time — the
    // UI renders "resumed · running for N min" from this instead of
    // presenting the replay as a from-scratch generation (Shenzhen-r2 B1).
    markResumedRun: (agentId: string, runId: string, startedAtMs: number) => {
      set((state) => ({
        agentSessions: updateSession(state.agentSessions, agentId, () => ({
          resumedRun: { runId, startedAtMs },
        })),
      }));
    },

    // Stop streaming and save to history for a specific agent
    stopStreaming: (agentId: string, agentName?: string, opts?: { cancelled?: boolean }) => {
      // OS-level completion notice (#44): the in-app toast is invisible when
      // the user is in another application, so the desktop build forwards a
      // system notification when the window has no focus. A user-cancelled
      // run is the user's own action — never notify for it. Read the
      // streaming flag BEFORE set() so the duplicate-call guard below also
      // guards the notification.
      const wasStreaming = getSession(get().agentSessions, agentId).isStreaming;
      if (
        wasStreaming &&
        !opts?.cancelled &&
        typeof document !== 'undefined' &&
        !document.hasFocus()
      ) {
        void notifyAgentReplyCompleted(agentName || agentId);
      }
      set((prevState) => {
        const session = getSession(prevState.agentSessions, agentId);

        // Prevent duplicate calls
        if (!session.isStreaming) return {};

        // Extract user-visible response (concatenate ALL owner-facing calls)
        const responseParts = session.currentToolCalls
          .filter((tool) => isOwnerReplyTool(tool.tool_name))
          .map((tool) => tool.tool_input?.content as string)
          // Blank content is no reply — same line the backend persist
          // guard draws: a "\n" part falls through to the placeholder
          // branch instead of rendering as a blank session bubble.
          .filter((s) => !isBlankText(s));

        let displayContent: string;
        let isError = false;
        if (responseParts.length > 0) {
          displayContent = responseParts.join('\n\n');
        } else if (session.currentErrors.length > 0) {
          displayContent = session.currentErrors.join('\n\n');
          isError = true;
        } else if (opts?.cancelled) {
          // The user cut the turn short themselves — "the agent decided not
          // to reply" is a lie in this branch (the agent never got to decide
          // anything). Write the same marker the backend persists for an
          // interrupted turn, so this bubble and its reloaded history row
          // agree; MessageBubble localizes both markers at render time
          // (see GitHub #87 and lib/turnMarkers.ts).
          displayContent = INTERRUPTED_MARKER;
        } else {
          displayContent = NO_RESPONSE_MARKER;
        }

        const userMessage = session.messages.find((m) => m.role === 'user');
        const warnings = !isError && session.currentErrors.length > 0
          ? [...session.currentErrors]
          : undefined;

        const assistantMessage: ChatMessage = {
          id: generateId(),
          role: 'assistant',
          content: displayContent,
          timestamp: Date.now(),
          // Stamp the run's event_id so the unified timeline can dedup
          // this session reply against the persisted history row (which
          // carries the same event_id) by exact (role, event_id) — immune
          // to whitespace/format drift between the two code paths.
          event_id: session.currentRunId ?? undefined,
          isError,
          // Self-serviceable reason only matters when the turn actually failed
          // (no reply). If a reply somehow came through, don't badge it.
          actionReason: isError ? session.currentActionReason ?? undefined : undefined,
          warnings,
          thinking: session.currentThinking || undefined,
          toolCalls: session.currentToolCalls.length > 0 ? [...session.currentToolCalls] : undefined,
          // Snapshot the streaming timeline onto the persisted message so
          // MessageBubble renders it under "View reasoning & tools" (same
          // affordance as historical messages). The just-finished turn is
          // therefore a normal history bubble from the moment it settles —
          // no separate flat-rendered "last turn" zone.
          timeline: session.currentEvents.length > 0 ? [...session.currentEvents] : undefined,
          // Cut the same events into segments: MessageBubble renders this
          // one record as the m things the agent actually said, each with
          // its own process. content keeps the joined full text —
          // notifications, copy and search still want one plain-text
          // carrier, and it is the fallback for older messages.
          segments: session.currentEvents.length > 0
            ? segmentTurn(session.currentEvents)
            : undefined,
        };

        // Mark all running steps as completed
        const completedSteps = session.currentSteps.map((step) => {
          if (step.status === 'running') {
            return {
              ...step,
              status: 'completed' as const,
              description: step.description.replace('Executing...', '✓ Done').replace('Running...', '✓ Done'),
            };
          }
          return step;
        });

        let newHistory = session.history;
        if (userMessage) {
          const round: ConversationRound = {
            id: generateId(),
            userMessage,
            assistantMessage,
            steps: completedSteps,
            timestamp: Date.now(),
          };
          newHistory = [round, ...session.history];
        }

        // Build notification state for background completion
        const isBackgroundAgent = agentId !== prevState.activeAgentId;
        const newCompletedIds = isBackgroundAgent && !prevState.completedAgentIds.includes(agentId)
          ? [...prevState.completedAgentIds, agentId]
          : prevState.completedAgentIds;
        const newToastQueue = isBackgroundAgent
          ? [...prevState.toastQueue, { kind: 'agent' as const, agentId, agentName: agentName || agentId, timestamp: Date.now() }]
          : prevState.toastQueue;

        // Reconcile any steer bubble still 'queued' at run end: the run is over
        // and will emit no further steer_consumed/steer_rejected for it (a steer
        // that arrives after the loop's final turn is never drained, and error /
        // circuit-open paths end the run with no ack at all), so a bubble left
        // 'queued' would hang forever. Flip it to 'rejected' with reason
        // 'run_ended' so the three-state invariant ("never a silently hung
        // queued") holds on every termination path, not just the ack paths.
        const reconciledMessages = patchSteerBubbles(
          session.messages,
          // Only bubbles still awaiting an ack — a 'queued' bubble never carries
          // a rejectReason yet, so we stamp 'run_ended' outright.
          (_id, m) => m.steerStatus === 'queued',
          () => ({ steerStatus: 'rejected', rejectReason: 'run_ended' }),
        );

        return {
          agentSessions: updateSession(prevState.agentSessions, agentId, () => ({
            messages: [...reconciledMessages, assistantMessage],
            currentSteps: completedSteps,
            history: newHistory,
            isStreaming: false,
            // Stream has ended; the snapshot of currentEvents is now
            // carried by `assistantMessage.timeline` above. Clear the
            // ephemeral buffer so the next turn starts clean.
            currentEvents: [],
          })),
          completedAgentIds: newCompletedIds,
          toastQueue: newToastQueue,
        };
      });
    },

    // Process incoming WebSocket message for a specific agent
    processMessage: (agentId: string, message: RuntimeMessage) => {
      switch (message.type) {
        case 'run_started': {
          // First meaningful frame of a fresh run — carries the run/event
          // id. Record it (and backfill it onto the user prompt) so the
          // turn's messages can be deduped against history by event_id.
          //
          // Also reset bookmark highlights for this agent: a new run begins,
          // so prior-run info highlights and non-badge sub-bookmarks are
          // cleared (spec §5.1).  Badge-backed items (failed jobs, inbox)
          // survive — see bookmarkStore.onRunStart for the exemption logic.
          //
          // This is the only correct wiring point: run_started is the
          // authoritative new-run signal and is never sent during a Phase C
          // reconnect (which uses run_reconnect instead), so we never
          // mistakenly reset on a same-run reconnect.
          get().setCurrentRunId(agentId, message.run_id);
          // Record whether this run accepts a mid-run steer — gates SENDING for
          // the run's duration (the composer stays editable regardless).
          set((state) => ({
            agentSessions: updateSession(state.agentSessions, agentId, () => ({
              currentSteerable: message.steerable === true,
            })),
          }));
          useBookmarkStore.getState().onRunStart(agentId);
          break;
        }
        case 'steer_queued': {
          // The backend accepted a mid-run follow-up into the run. The optimistic
          // bubble was already added as 'queued' at send time (addSteerMessage);
          // this ack just confirms it, so nothing to change. (Kept explicit so a
          // future "sending…" → "queued" transition has a home.)
          break;
        }
        case 'steer_consumed': {
          // The run actually folded these follow-ups into the turn — flip their
          // bubbles from 'queued' to 'merged' by the ids the client minted.
          const ids = new Set(message.ids || []);
          set((state) => ({
            agentSessions: updateSession(state.agentSessions, agentId, (s) => ({
              messages: patchSteerBubbles(
                s.messages,
                (id) => ids.has(id),
                () => ({ steerStatus: 'merged' }),
              ),
            })),
          }));
          break;
        }
        case 'steer_rejected': {
          // Bounds (too large / too many pending) or the framework can't steer —
          // mark that bubble rejected so the user sees it did NOT land (never a
          // silently hung 'queued').
          set((state) => ({
            agentSessions: updateSession(state.agentSessions, agentId, (s) => ({
              messages: patchSteerBubbles(
                s.messages,
                (id) => id === message.client_msg_id,
                () => ({ steerStatus: 'rejected', rejectReason: message.reason }),
              ),
            })),
          }));
          break;
        }
        case 'run_reconnect':
          // Normally absorbed upstream (wsManager.translateReconnectFrame
          // returns null for run_reconnect, so it never reaches here). Kept as
          // an explicit no-op — a reconnected session holds no live channel, so
          // there is deliberately no bubble/steer state to change — rather than
          // relying on the switch's silent fall-through to say the same thing.
          break;
        case 'progress': {
          const progress = message as ProgressMessage;
          set((state) => {
            const session = getSession(state.agentSessions, agentId);
            const existingIndex = session.currentSteps.findIndex(
              (s) => s.step === progress.step
            );
            const step: Step = {
              id: progress.step,
              step: progress.step,
              title: progress.title,
              description: progress.description,
              status: progress.status,
              substeps: progress.substeps,
              details: progress.details,
              timestamp: progress.timestamp,
            };

            let newToolCalls = session.currentToolCalls;
            const newEvents: TurnEvent[] = [...session.currentEvents];
            const toolName = (progress.details?.tool_name as string | undefined) || '';
            const args = (progress.details?.arguments as Record<string, unknown> | undefined) || undefined;
            const rawOutput = progress.details?.output;
            const outputStr = typeof rawOutput === 'string'
              ? rawOutput
              : rawOutput !== undefined && rawOutput !== null
                ? JSON.stringify(rawOutput)
                : undefined;

            if (toolName && args) {
              const toolCall: AgentToolCall = {
                type: 'tool_call',
                timestamp: progress.timestamp,
                tool_name: toolName,
                tool_input: args,
                step: progress.step,
                // Replacement key: the pending and completed forms share one tool_call_id.
                tool_call_id: (progress.details?.tool_call_id as string | undefined),
              };
              // A name-first pending entry must be replaced IN PLACE by the
              // completed call — never kept as a second row. currentToolCalls
              // is the reply-extraction source (stopStreaming picks
              // the owner-facing content out of it), so a
              // duplicate with empty arguments would inject an empty reply
              // segment.
              const callId = (progress.details?.tool_call_id as string | undefined);
              const sameCallIdx = callId
                ? session.currentToolCalls.findIndex(
                    (t) => (t as AgentToolCall & { tool_call_id?: string }).tool_call_id === callId,
                  )
                : -1;
              const exists = sameCallIdx < 0 && session.currentToolCalls.some(
                (t) => t.tool_name === toolCall.tool_name && t.timestamp === toolCall.timestamp
              );
              if (sameCallIdx >= 0) {
                newToolCalls = [...session.currentToolCalls];
                newToolCalls[sameCallIdx] = toolCall;
              }
              if (!exists) {
                if (sameCallIdx < 0) newToolCalls = [...session.currentToolCalls, toolCall];

                // Inline timeline: an owner-facing tool
                // call carries the agent's actual reply in its content
                // arg — surface it as a `reply` event so <TurnTimeline>
                // can render it as the primary user-facing block.
                if (isOwnerReplyTool(toolName)) {
                  // NexusPower streams this reply live (agent_reply_delta)
                  // and the completed call repeats the same final text —
                  // fold it into the open streaming bubble instead of
                  // pushing a duplicate. call_id is the join key; when it
                  // is absent (claude/codex, which never stream args) we
                  // fall back to the historical "push a new block".
                  const callId = progress.details?.tool_call_id as string | undefined;
                  const openIndex = newEvents.findIndex(
                    (ev) =>
                      ev.type === 'reply' &&
                      ev.streaming === true &&
                      (callId ? ev.call_id === callId : true),
                  );
                  if (openIndex >= 0) {
                    const open = newEvents[openIndex] as Extract<TurnEvent, { type: 'reply' }>;
                    newEvents[openIndex] = {
                      ...open,
                      // The completed arguments are authoritative.
                      content: (args.content as string) || open.content,
                      streaming: false,
                      reply_via: (progress.details?.reply_via as string | undefined),
                    };
                  } else {
                    newEvents.push({
                      type: 'reply',
                      id: generateId(),
                      ts: progress.timestamp,
                      content: (args.content as string) || '',
                      reply_via: (progress.details?.reply_via as string | undefined),
                      call_id: callId,
                    });
                  }
                } else {
                  // One row per call: the pending form lands first and the
                  // completed form overwrites it by tool_call_id (the event id
                  // is preserved so React updates the row instead of
                  // remounting it).
                  const pendingIdx = callId
                    ? newEvents.findIndex(
                        (e) => e.type === 'tool_call' && e.tool_call_id === callId,
                      )
                    : -1;
                  const nextCall: TurnEvent = {
                    type: 'tool_call',
                    id: pendingIdx >= 0 ? newEvents[pendingIdx].id : generateId(),
                    ts: progress.timestamp,
                    tool_name: toolName,
                    tool_input: args,
                    tool_call_id: callId,
                    pending: !!progress.details?.pending,
                  };
                  if (pendingIdx >= 0) newEvents[pendingIdx] = nextCall;
                  else newEvents.push(nextCall);
                }
              }
            } else if (outputStr !== undefined) {
              // tool_output frame: backend emits a progress message with
              // `details.output` and the SAME `step` as the originating
              // tool_call (response_processor.py:410 uses 3.4.{N} for
              // both, with N matching by arrival order). We backfill
              // tool_output onto the matching tool_call so downstream
              // consumers — ArtifactToolCallCards, MessageBubble's
              // reasoning panel, anything else that branches on
              // tc.tool_output — work mid-stream instead of waiting for
              // history reload to reconstruct the linkage.
              //
              // Match strategy: prefer exact `step` equality; fall back
              // to "latest tool_call without tool_output yet" only when
              // step is absent (defensive — shouldn't happen with the
              // current backend, but tolerates older streams and the
              // reconnect-replay path which uses the same step value).
              const matchByStep = progress.step
                ? newToolCalls.findIndex((tc) => tc.step === progress.step && !tc.tool_output)
                : -1;
              const matchIdx = matchByStep >= 0
                ? matchByStep
                : (() => {
                    for (let i = newToolCalls.length - 1; i >= 0; i--) {
                      if (!newToolCalls[i].tool_output) return i;
                    }
                    return -1;
                  })();
              if (matchIdx >= 0) {
                newToolCalls = newToolCalls.map((tc, i) =>
                  i === matchIdx ? { ...tc, tool_output: outputStr } : tc,
                );
                // Also surface the output to TurnTimeline so live UI
                // shows the "Execution completed" block, mirroring how
                // historical turns render after persistence.
                const tcMatched = newToolCalls[matchIdx];
                newEvents.push({
                  type: 'tool_output',
                  id: generateId(),
                  ts: progress.timestamp,
                  tool_name: tcMatched.tool_name,
                  output: outputStr,
                });
              }
            }

            const newSteps = existingIndex >= 0
              ? session.currentSteps.map((s, i) => i === existingIndex ? step : s)
              : [...session.currentSteps, step];

            return {
              agentSessions: updateSession(state.agentSessions, agentId, () => ({
                currentSteps: newSteps,
                currentToolCalls: newToolCalls,
                currentEvents: newEvents,
              })),
            };
          });
          break;
        }

        case 'agent_response': {
          const delta = message as AgentTextDelta;
          set((state) => {
            const session = getSession(state.agentSessions, agentId);

            // Dedup against the agent's own duplication habit (see
            // 2026-05-12 review): thinking models often emit a
            // condensed paraphrase via native LLM output *after*
            // they've already spoken to the owner.
            // The reply is the authoritative version; the native
            // paraphrase repeats the same information in a degraded
            // form. Drop the post-reply native_output here.
            //
            // TODO (post-launch): preferred long-term fix is a prompt
            // constraint telling the agent not to repeat. Once that
            // lands and noise drops to ~0, this dedup can be removed.
            const alreadyReplied = session.currentEvents.some(
              (ev) => ev.type === 'reply',
            );
            if (alreadyReplied) {
              return {};
            }

            // Coalesce native_output deltas into ONE growing bubble per
            // "phase" (between tool_call/reply boundaries). Some models
            // emit native text and thinking interleaved at the delta
            // level (e.g. DeepSeek-V4 with visible reasoning); without
            // this, every token would be its own bubble — the bug Bin
            // hit on agent_5d8962… 2026-05-12. We walk backwards from
            // the tail, skipping over thinking events (they're a
            // peer-stream, not an interruption), and merge into the
            // most-recent native_output. tool_call or reply mark a
            // genuine boundary and reset the search.
            const events = session.currentEvents;
            let openIdx = -1;
            for (let i = events.length - 1; i >= 0; i--) {
              const t = events[i].type;
              if (t === 'tool_call' || t === 'reply') break;
              if (t === 'native_output') { openIdx = i; break; }
            }

            if (openIdx >= 0) {
              const open = events[openIdx] as Extract<TurnEvent, { type: 'native_output' }>;
              const merged: TurnEvent = { ...open, content: open.content + delta.delta };
              return {
                agentSessions: updateSession(state.agentSessions, agentId, (s) => ({
                  currentAssistantMessage: s.currentAssistantMessage + delta.delta,
                  currentEvents: [
                    ...s.currentEvents.slice(0, openIdx),
                    merged,
                    ...s.currentEvents.slice(openIdx + 1),
                  ],
                })),
              };
            }

            const nextEvent: TurnEvent = {
              type: 'native_output',
              id: generateId(),
              ts: delta.timestamp ?? Date.now() / 1000,
              content: delta.delta,
            };
            return {
              agentSessions: updateSession(state.agentSessions, agentId, (s) => ({
                currentAssistantMessage: s.currentAssistantMessage + delta.delta,
                currentEvents: [...s.currentEvents, nextEvent],
              })),
            };
          });
          break;
        }

        case 'agent_thinking': {
          // Thinking is delivered as a delta stream. We coalesce all
          // deltas of the current "phase" (between tool_call/reply
          // boundaries) into ONE bubble.
          //
          // Naive coalesce (`if last.type === 'thinking', merge`) is
          // wrong for models that interleave thinking with native_output
          // at the delta level (e.g. agent_5d8962… on 2026-05-12 was on
          // a model that streamed both → every thinking delta saw the
          // previous event as native_output → fell through to "new
          // bubble" → user saw 50+ separate thinking blocks pop in).
          // Walk backwards from the tail and skip over peer-stream
          // events (native_output, other thinking) to find the open
          // thinking bubble; only tool_call/reply truly interrupt the
          // thought train.
          const thinking = message as AgentThinking;
          const delta = thinking.thinking_content;
          if (!delta) break;
          // Tier (design A′): NexusPower's own narration renders at the
          // PROGRESS tier, provider CoT keeps receding. A tier switch is a
          // block boundary — a block carries ONE tier flag, so merging across
          // one would label two tiers of text with a single one (dimming the
          // narration, or brightening the scratchpad).
          //
          // Every path carries a real tier now — live frames, reconnect
          // replay and event-log reload alike (run_recorder tags the segment,
          // wsManager translates it). So this is a plain boolean again; the
          // "unknown tier" state that used to guard the replay seam is gone
          // with the gap that needed it.
          const isMonologue = isMonologueFrame(delta, thinking.monologue);
          set((state) => ({
            agentSessions: updateSession(state.agentSessions, agentId, (s) => {
              const events = s.currentEvents;
              let openIdx = -1;
              for (let i = events.length - 1; i >= 0; i--) {
                const t = events[i].type;
                if (t === 'tool_call' || t === 'reply') break;
                if (t === 'thinking') { openIdx = i; break; }
              }
              const open =
                openIdx >= 0
                  ? (events[openIdx] as Extract<TurnEvent, { type: 'thinking' }>)
                  : null;
              // A tier switch is a block boundary. It can only fall where the
              // tier genuinely changed: both the WS batcher and run_recorder
              // refuse to coalesce across a switch, so no frame arriving here
              // straddles one.
              if (open && !!open.monologue === isMonologue) {
                const merged: TurnEvent = { ...open, content: open.content + delta };
                return {
                  currentThinking: s.currentThinking + delta,
                  currentEvents: [
                    ...events.slice(0, openIdx),
                    merged,
                    ...events.slice(openIdx + 1),
                  ],
                };
              }
              // No open thinking bubble in the current phase, or the open one
              // is the other tier — start one.
              const nextEvent: TurnEvent = {
                type: 'thinking',
                id: generateId(),
                ts: thinking.timestamp ?? Date.now() / 1000,
                content: delta,
                monologue: isMonologue,
              };
              return {
                currentThinking: s.currentThinking + delta,
                currentEvents: [...events, nextEvent],
              };
            }),
          }));
          break;
        }

        case 'agent_reply_delta': {
          // NexusPower only: the reply itself, streaming. Under that
          // framework's contract the agent's plain text is private
          // reasoning; the reply lives in an expression tool's argument,
          // and this stream is that argument being written. We grow ONE
          // reply bubble keyed by call_id — the completed tool_call
          // frame later folds into the same block (see 'progress'),
          // so the user sees one message that filled in live instead of
          // a duplicate appearing at the end.
          const replyDelta = message as AgentReplyDelta;
          if (!replyDelta.delta) break;
          set((state) => {
            const session = getSession(state.agentSessions, agentId);
            const events = [...session.currentEvents];
            const openIndex = events.findIndex(
              (ev) =>
                ev.type === 'reply' &&
                ev.streaming === true &&
                ev.call_id === replyDelta.call_id,
            );
            if (openIndex >= 0) {
              const open = events[openIndex] as Extract<TurnEvent, { type: 'reply' }>;
              events[openIndex] = { ...open, content: open.content + replyDelta.delta };
            } else {
              events.push({
                type: 'reply',
                id: generateId(),
                ts: Date.now(),
                content: replyDelta.delta,
                call_id: replyDelta.call_id,
                streaming: true,
              });
            }
            return {
              agentSessions: updateSession(state.agentSessions, agentId, () => ({
                currentEvents: events,
              })),
            };
          });
          break;
        }

        case 'agent_plan': {
          // NexusPower only: full plan snapshot, replace-on-write. One
          // block per turn so the user watches steps flip state rather
          // than seeing a new plan card per update.
          const planMsg = message as AgentPlanMessage;
          set((state) => {
            const session = getSession(state.agentSessions, agentId);
            const events = [...session.currentEvents];
            const existing = events.findIndex((ev) => ev.type === 'plan');
            const block: TurnEvent = {
              type: 'plan',
              id: existing >= 0 ? events[existing].id : generateId(),
              ts: Date.now(),
              steps: planMsg.steps || [],
              note: planMsg.note,
            };
            if (existing >= 0) events[existing] = block;
            else events.push(block);
            return {
              agentSessions: updateSession(state.agentSessions, agentId, () => ({
                currentEvents: events,
              })),
            };
          });
          break;
        }

        case 'tool_call': {
          const toolCall = message as AgentToolCall;
          set((state) => ({
            agentSessions: updateSession(state.agentSessions, agentId, (s) => ({
              currentToolCalls: [...s.currentToolCalls, toolCall],
            })),
          }));
          break;
        }

        case 'error': {
          const errorMsg = message as ErrorMessage;
          const errorText = errorMsg.error_message || 'Unknown error occurred';
          console.error(`Runtime error [${agentId}]:`, errorText);
          // A config_actionable (deterministic self-serviceable) OR an
          // infra_transient (platform-side executor OOM / unreachable) error
          // carries a reason so the UI can show "what you can do" guidance.
          // Latch the most recent one for this turn; stopStreaming stamps it
          // onto the assistant message. The reason value (context_window /
          // executor_oom / …) is what MessageBubble keys its copy + badge on.
          const actionReason =
            errorMsg.error_type === 'config_actionable'
              ? errorMsg.action_reason ?? 'config_actionable'
              : errorMsg.error_type === 'infra_transient'
                ? errorMsg.action_reason ?? 'infra_transient'
                : undefined;
          set((state) => ({
            agentSessions: updateSession(state.agentSessions, agentId, (s) => ({
              currentErrors: [...s.currentErrors, errorText],
              ...(actionReason ? { currentActionReason: actionReason } : {}),
            })),
          }));
          break;
        }

        case 'complete': {
          const session = getSession(get().agentSessions, agentId);
          const hasReply = session.currentToolCalls.some(
            (tool) =>
              isOwnerReplyTool(tool.tool_name) &&
              !isBlankText(tool.tool_input?.content as string),
          );
          get().stopStreaming(agentId);
          if (hasReply) {
            captureProductEvent('reply_rendered', {
              agent_id: agentId,
              run_id: session.currentRunId ?? undefined,
              trigger_source: 'chat',
            });
          }
          break;
        }

        case 'cancelled': {
          // User-initiated cancellation — stop streaming gracefully,
          // and never fire the OS completion notification for it.
          get().stopStreaming(agentId, undefined, { cancelled: true });
          break;
        }
      }
    },

    // Clear a specific agent's session
    clearAgent: (agentId: string) => {
      set((state) => {
        const newSessions = { ...state.agentSessions };
        delete newSessions[agentId];
        return { agentSessions: newSessions };
      });
    },

    // Clear all sessions
    clearAll: () => {
      set({
        agentSessions: {},
        activeAgentId: '',
        completedAgentIds: [],
        toastQueue: [],
      });
    },

    // Force mounted ChatPanel(s) to reload server history (post-wipe refresh).
    requestHistoryRefresh: () => {
      set((state) => ({ historyRefreshTick: state.historyRefreshTick + 1 }));
    },

    // Same, for the team workspace panel (post-wipe refresh of files/artifacts).
    requestWorkspaceRefresh: () => {
      set((state) => ({ workspaceRefreshTick: state.workspaceRefreshTick + 1 }));
    },

    // Notification actions
    pushToast: (item: ToastItem) => {
      set((state) => ({
        toastQueue: [
          ...state.toastQueue.filter((t) => toastKey(t) !== toastKey(item)),
          item,
        ],
      }));
    },

    dismissToast: (key: string) => {
      set((state) => ({
        toastQueue: state.toastQueue.filter((t) => toastKey(t) !== key),
      }));
    },

    clearCompletedNotification: (agentId: string) => {
      set((state) => ({
        completedAgentIds: state.completedAgentIds.filter((id) => id !== agentId),
      }));
    },

    // Query helpers
    isAgentStreaming: (agentId: string) => {
      return getSession(get().agentSessions, agentId).isStreaming;
    },

    runningAgentIds: () => {
      const sessions = get().agentSessions;
      return Object.keys(sessions).filter((id) => sessions[id].isStreaming);
    },
  };
});
