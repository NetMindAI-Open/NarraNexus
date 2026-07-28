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
  AgentToolCall,
  ErrorMessage,
  TurnEvent,
} from '@/types';
import { generateId } from '@/lib/utils';
import { notifyAgentReplyCompleted } from '@/lib/desktopNotify';

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
}

/** Toast notification for background-completed agents */
export interface ToastItem {
  agentId: string;
  agentName: string;
  timestamp: number;
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

  getUserVisibleResponse: () => string | null;

  // Actions (all accept agentId)
  setActiveAgent: (agentId: string) => void;
  addUserMessage: (
    agentId: string,
    content: string,
    attachments?: import('@/types').Attachment[],
    timestampMs?: number,
  ) => string;
  startStreaming: (agentId: string) => void;
  stopStreaming: (agentId: string, agentName?: string, opts?: { cancelled?: boolean }) => void;
  /** Set the current run's event_id and backfill it onto the trailing
   *  event-id-less user message. Called from the `run_started` frame
   *  (fresh run) and from wsManager on reconnect. */
  setCurrentRunId: (agentId: string, runId: string) => void;
  processMessage: (agentId: string, message: RuntimeMessage) => void;
  clearAgent: (agentId: string) => void;
  clearAll: () => void;
  /** Force any mounted ChatPanel to reload its server history. */
  requestHistoryRefresh: () => void;

  // Notification actions
  dismissToast: (agentId: string) => void;
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
    completedAgentIds: [],
    toastQueue: [],

    // Initial flat fields (derived from empty active session)
    ...deriveFlatFields({ agentSessions: {}, activeAgentId: '' }),

    getUserVisibleResponse: () => {
      const state = get();
      const session = getSession(state.agentSessions, state.activeAgentId);
      const parts = session.currentToolCalls
        .filter((tool) => tool.tool_name.endsWith('send_message_to_user_directly'))
        .map((tool) => tool.tool_input?.content as string)
        .filter(Boolean);
      return parts.length > 0 ? parts.join('\n\n') : null;
    },

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

        // Extract user-visible response (concatenate ALL send_message_to_user_directly calls)
        const responseParts = session.currentToolCalls
          .filter((tool) => tool.tool_name.endsWith('send_message_to_user_directly'))
          .map((tool) => tool.tool_input?.content as string)
          .filter(Boolean);

        let displayContent: string;
        let isError = false;
        if (responseParts.length > 0) {
          displayContent = responseParts.join('\n\n');
        } else if (session.currentErrors.length > 0) {
          displayContent = session.currentErrors.join('\n\n');
          isError = true;
        } else {
          displayContent = '(Agent decided no response needed)';
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
          ? [...prevState.toastQueue, { agentId, agentName: agentName || agentId, timestamp: Date.now() }]
          : prevState.toastQueue;

        return {
          agentSessions: updateSession(prevState.agentSessions, agentId, () => ({
            messages: [...session.messages, assistantMessage],
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
          useBookmarkStore.getState().onRunStart(agentId);
          break;
        }
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
              };
              const exists = session.currentToolCalls.some(
                (t) => t.tool_name === toolCall.tool_name && t.timestamp === toolCall.timestamp
              );
              if (!exists) {
                newToolCalls = [...session.currentToolCalls, toolCall];

                // Inline timeline: a send_message_to_user_directly tool
                // call carries the agent's actual reply in its content
                // arg — surface it as a `reply` event so <TurnTimeline>
                // can render it as the primary user-facing block.
                if (toolName.includes('send_message_to_user_directly')) {
                  newEvents.push({
                    type: 'reply',
                    id: generateId(),
                    ts: progress.timestamp,
                    content: (args.content as string) || '',
                    reply_via: (progress.details?.reply_via as string | undefined),
                  });
                } else {
                  newEvents.push({
                    type: 'tool_call',
                    id: generateId(),
                    ts: progress.timestamp,
                    tool_name: toolName,
                    tool_input: args,
                    tool_call_id: (progress.details?.tool_call_id as string | undefined),
                  });
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
            // they've already called send_message_to_user_directly.
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
          set((state) => ({
            agentSessions: updateSession(state.agentSessions, agentId, (s) => {
              const events = s.currentEvents;
              let openIdx = -1;
              for (let i = events.length - 1; i >= 0; i--) {
                const t = events[i].type;
                if (t === 'tool_call' || t === 'reply') break;
                if (t === 'thinking') { openIdx = i; break; }
              }
              if (openIdx >= 0) {
                const open = events[openIdx] as Extract<TurnEvent, { type: 'thinking' }>;
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
              // No open thinking bubble in the current phase — start one.
              const nextEvent: TurnEvent = {
                type: 'thinking',
                id: generateId(),
                ts: thinking.timestamp ?? Date.now() / 1000,
                content: delta,
              };
              return {
                currentThinking: s.currentThinking + delta,
                currentEvents: [...events, nextEvent],
              };
            }),
          }));
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
          get().stopStreaming(agentId);
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

    // Notification actions
    dismissToast: (agentId: string) => {
      set((state) => ({
        toastQueue: state.toastQueue.filter((t) => t.agentId !== agentId),
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
