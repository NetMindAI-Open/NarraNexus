/**
 * Chat store — multi-agent concurrent session management
 *
 * Core design: agentSessions map indexed by agentId, each agent has independent chat state.
 * Flat top-level fields (messages, isStreaming, etc.) are auto-derived from activeAgentId's
 * session after every set() call, preserving backward compatibility for consumers.
 */

import { create } from 'zustand';
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
} from '@/types';
import { generateId } from '@/lib/utils';

// Total AgentRuntime pipeline steps: 0 (init), 1 (narrative), 2 (modules),
// 3 (agent loop), 4 (persist), 5 (background hooks — completes instantly).
// Steps 5-6 (hooks + callbacks) run in background and don't block the UI.
const TOTAL_PIPELINE_STEPS = 5;

/** Per-agent independent chat state */
export interface AgentChatState {
  messages: ChatMessage[];
  currentSteps: Step[];
  currentThinking: string;
  currentToolCalls: AgentToolCall[];
  currentErrors: string[];
  currentAssistantMessage: string;
  isStreaming: boolean;
  history: ConversationRound[];
  totalSteps: number;
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
  currentAssistantMessage: '',
  isStreaming: false,
  history: Object.freeze([]) as unknown as ConversationRound[],
  totalSteps: TOTAL_PIPELINE_STEPS,
});

/** Create a fresh mutable state for a new agent session */
function createDefaultAgentState(): AgentChatState {
  return {
    messages: [],
    currentSteps: [],
    currentThinking: '',
    currentToolCalls: [],
    currentErrors: [],
    currentAssistantMessage: '',
    isStreaming: false,
    history: [],
    totalSteps: TOTAL_PIPELINE_STEPS,
  };
}

interface ChatState {
  // Multi-agent session map
  agentSessions: Record<string, AgentChatState>;
  activeAgentId: string;

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
  totalSteps: number;

  getUserVisibleResponse: () => string | null;

  // Actions (all accept agentId)
  setActiveAgent: (agentId: string) => void;
  addUserMessage: (agentId: string, content: string) => string;
  startStreaming: (agentId: string) => void;
  stopStreaming: (agentId: string, agentName?: string) => void;
  processMessage: (agentId: string, message: RuntimeMessage) => void;
  clearAgent: (agentId: string) => void;
  clearAll: () => void;

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
    totalSteps: session.totalSteps,
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

    // Add user message to a specific agent's session
    addUserMessage: (agentId: string, content: string) => {
      const id = generateId();
      const message: ChatMessage = {
        id,
        role: 'user',
        content,
        timestamp: Date.now(),
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
        })),
      }));
    },

    // Stop streaming and save to history for a specific agent
    stopStreaming: (agentId: string, agentName?: string) => {
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
          isError,
          warnings,
          thinking: session.currentThinking || undefined,
          toolCalls: session.currentToolCalls.length > 0 ? [...session.currentToolCalls] : undefined,
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
          })),
          completedAgentIds: newCompletedIds,
          toastQueue: newToastQueue,
        };
      });
    },

    // Process incoming WebSocket message for a specific agent
    processMessage: (agentId: string, message: RuntimeMessage) => {
      switch (message.type) {
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
            if (progress.details?.tool_name && progress.details?.arguments) {
              const toolCall: AgentToolCall = {
                type: 'tool_call',
                timestamp: progress.timestamp,
                tool_name: progress.details.tool_name as string,
                tool_input: progress.details.arguments as Record<string, unknown>,
              };
              const exists = session.currentToolCalls.some(
                (t) => t.tool_name === toolCall.tool_name && t.timestamp === toolCall.timestamp
              );
              if (!exists) {
                newToolCalls = [...session.currentToolCalls, toolCall];
              }
            }

            const newSteps = existingIndex >= 0
              ? session.currentSteps.map((s, i) => i === existingIndex ? step : s)
              : [...session.currentSteps, step];

            return {
              agentSessions: updateSession(state.agentSessions, agentId, () => ({
                currentSteps: newSteps,
                currentToolCalls: newToolCalls,
              })),
            };
          });
          break;
        }

        case 'agent_response': {
          const delta = message as AgentTextDelta;
          set((state) => ({
            agentSessions: updateSession(state.agentSessions, agentId, (s) => ({
              currentAssistantMessage: s.currentAssistantMessage + delta.delta,
            })),
          }));
          break;
        }

        case 'agent_thinking': {
          const thinking = message as AgentThinking;
          set((state) => ({
            agentSessions: updateSession(state.agentSessions, agentId, (s) => ({
              currentThinking: s.currentThinking + thinking.thinking_content,
            })),
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
          set((state) => ({
            agentSessions: updateSession(state.agentSessions, agentId, (s) => ({
              currentErrors: [...s.currentErrors, errorText],
            })),
          }));
          break;
        }

        case 'complete': {
          get().stopStreaming(agentId);
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
