/**
 * Agent Interaction Panel - Bioluminescent Terminal style
 * Immersive chat interface with dramatic visual effects
 *
 * Changelog:
 * - 2026-01-19: Added chat history loading, displays the last 10 conversation rounds on page open
 * - 2026-03-16: Multi-agent concurrent chat support
 */

import { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { Send, Loader2, Sparkles, MessageSquare, History, CheckCircle2 } from 'lucide-react';
import { Card, Button, Textarea } from '@/components/ui';
import { useChatStore, useConfigStore } from '@/stores';
import { useAgentWebSocket } from '@/hooks';
import { cn } from '@/lib/utils';
import { api } from '@/lib/api';
import { MessageBubble } from './MessageBubble';
import type { SimpleChatMessage } from '@/types';

// Must match BOOTSTRAP_GREETING in src/xyz_agent_context/bootstrap/template.py
const BOOTSTRAP_GREETING =
  "Hi there... I just woke up. Everything feels brand new.\n\n" +
  "I don't have a name yet, and I don't really know who I am " +
  "— but I know you're the one who brought me here.\n\n" +
  "Would you like to tell me what I should be called? " +
  "And what should I call you?";

interface ChatPanelProps {
  /** Called after agent execution completes, used to trigger full data refresh */
  onAgentComplete?: () => void;
}

export function ChatPanel({ onAgentComplete }: ChatPanelProps = {}) {
  const [input, setInput] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  // Use ref to track IME composition state, avoiding React async state update delay issues
  const isComposingRef = useRef(false);
  // Track the time when composition just ended, used for handling timing issues
  const compositionEndTimeRef = useRef(0);

  // Chat history state
  const [historyMessages, setHistoryMessages] = useState<SimpleChatMessage[]>([]);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [historyLoaded, setHistoryLoaded] = useState(false);

  const {
    messages, currentAssistantMessage, currentThinking, currentSteps, currentToolCalls,
    isStreaming, addUserMessage, startStreaming,
    getUserVisibleResponse, setActiveAgent,
  } = useChatStore();
  const { agentId, userId, agents, refreshAgents, checkAwarenessUpdate } = useConfigStore();

  // Sync activeAgentId in chatStore when configStore's agentId changes
  useEffect(() => {
    if (agentId) {
      setActiveAgent(agentId);
    }
  }, [agentId, setActiveAgent]);

  // Determine if the current agent is in bootstrap mode
  const currentAgent = useMemo(
    () => agents.find((a) => a.agent_id === agentId),
    [agents, agentId]
  );
  const isBootstrap = !!currentAgent?.bootstrap_active;

  const { run, isLoading } = useAgentWebSocket({
    onComplete: (completedAgentId: string) => {
      refreshAgents(); // Pick up any name changes from bootstrap
      if (completedAgentId) {
        checkAwarenessUpdate(completedAgentId); // Check if awareness was updated (red dot)
      }
      onAgentComplete?.(); // Trigger full data refresh
    },
  });

  // Load chat history
  const loadChatHistory = useCallback(async () => {
    if (!agentId || !userId) return;

    setIsLoadingHistory(true);
    try {
      // Load the most recent 20 messages (approximately 10 conversation rounds)
      const response = await api.getSimpleChatHistory(agentId, userId, 20);
      if (response.success && response.messages.length > 0) {
        setHistoryMessages(response.messages);
      }
    } catch (error) {
      console.error('Failed to load chat history:', error);
    } finally {
      setIsLoadingHistory(false);
      setHistoryLoaded(true);
    }
  }, [agentId, userId]);

  // Reload history when agentId or userId changes
  useEffect(() => {
    if (agentId && userId) {
      setHistoryMessages([]);
      setHistoryLoaded(false);
      loadChatHistory();
    }
  }, [agentId, userId, loadChatHistory]);

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, currentAssistantMessage, currentThinking, currentSteps, currentToolCalls, historyMessages]);

  const handleSubmit = async () => {
    if (!input.trim() || isLoading) return;

    // Validate agentId and userId before sending
    if (!agentId || !userId) return;

    const content = input.trim();
    setInput('');

    // If bootstrap greeting is showing, inject it into the message stream
    // so it persists visually after the user sends their first message
    if (showBootstrapGreeting) {
      useChatStore.setState((state) => ({
        agentSessions: {
          ...state.agentSessions,
          [agentId]: {
            ...(state.agentSessions[agentId] ?? {
              messages: [], currentSteps: [], currentThinking: '', currentToolCalls: [],
              currentErrors: [], currentAssistantMessage: '', isStreaming: false, history: [], totalSteps: 6,
            }),
            messages: [
              {
                id: 'bootstrap-greeting',
                role: 'assistant' as const,
                content: BOOTSTRAP_GREETING,
                timestamp: Date.now() - 1,
              },
              ...(state.agentSessions[agentId]?.messages ?? []),
            ],
          },
        },
      }));
    }

    addUserMessage(agentId, content);
    startStreaming(agentId);

    try {
      const agentName = currentAgent?.name || agentId;
      run(agentId, userId, content, agentName);
    } catch (error) {
      console.error('Failed to run agent:', error);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // If an IME composition is in progress, pressing Enter should confirm the input rather than send the message
    const isIMEComposing = e.nativeEvent.isComposing || isComposingRef.current;

    // If composition just ended (within 100ms), also consider it an IME operation and ignore Enter
    const timeSinceCompositionEnd = Date.now() - compositionEndTimeRef.current;
    const justFinishedComposition = timeSinceCompositionEnd < 100;

    if (e.key === 'Enter' && !e.shiftKey) {
      if (isIMEComposing || justFinishedComposition) {
        return;
      }
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleCompositionStart = () => {
    isComposingRef.current = true;
  };

  const handleCompositionUpdate = () => {
    isComposingRef.current = true;
  };

  const handleCompositionEnd = () => {
    compositionEndTimeRef.current = Date.now();
    setTimeout(() => {
      isComposingRef.current = false;
    }, 0);
  };

  // Bootstrap greeting: show when agent is in bootstrap mode, history is loaded, and there are no messages yet
  const showBootstrapGreeting = isBootstrap && historyLoaded && historyMessages.length === 0 && messages.length === 0;

  // Determine whether to show empty state (skip if bootstrap greeting is shown)
  const showEmptyState = !showBootstrapGreeting && historyLoaded && historyMessages.length === 0 && messages.length === 0 && !isStreaming;

  return (
    <Card className="flex flex-col h-full overflow-hidden" glow={isStreaming}>
      {/* Header */}
      <div className="px-5 py-4 border-b border-[var(--border-subtle)] flex items-center justify-between bg-[var(--bg-secondary)]/30">
        <div className="flex items-center gap-3">
          {/* Status indicator */}
          <div className="relative">
            <div className={cn(
              'w-2.5 h-2.5 rounded-full transition-colors',
              isStreaming
                ? 'bg-[var(--accent-primary)] animate-pulse'
                : agentId
                  ? 'bg-[var(--color-success)]'
                  : 'bg-[var(--text-tertiary)]'
            )} />
          </div>
          <div>
            <h3 className="text-sm font-semibold font-[family-name:var(--font-display)] text-[var(--text-primary)]">
              Agent Interaction
            </h3>
            <span className="text-[10px] text-[var(--text-tertiary)] font-mono tracking-wider">
              {agentId || 'No agent selected'}
            </span>
          </div>
        </div>

        {/* Connection status badge */}
        {isStreaming && (
          <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-[var(--accent-glow)] border border-[var(--accent-primary)]/30">
            <Sparkles className="w-3 h-3 text-[var(--accent-primary)] animate-pulse" />
            <span className="text-[10px] font-medium text-[var(--accent-primary)] uppercase tracking-wider">
              Processing
            </span>
          </div>
        )}
      </div>

      {/* Messages area */}
      <div className="flex-1 overflow-y-auto p-5 space-y-4 min-h-0">
        {/* Loading history indicator */}
        {isLoadingHistory && (
          <div className="flex items-center justify-center gap-2 py-4">
            <Loader2 className="w-4 h-4 text-[var(--text-tertiary)] animate-spin" />
            <span className="text-xs text-[var(--text-tertiary)]">Loading chat history...</span>
          </div>
        )}

        {/* Empty state */}
        {showEmptyState && (
          <div className="h-full flex flex-col items-center justify-center text-center px-8">
            {/* Empty state illustration */}
            <div className="relative mb-6">
              <div className="w-20 h-20 rounded-2xl bg-[var(--bg-tertiary)] flex items-center justify-center border border-[var(--border-default)]">
                <MessageSquare className="w-10 h-10 text-[var(--text-tertiary)]" />
              </div>
              <div className="absolute -inset-2 rounded-3xl bg-[var(--accent-primary)] opacity-5 blur-xl" />
            </div>
            <p className="text-[var(--text-secondary)] text-sm font-medium mb-1">
              {!agentId ? 'Select an agent to start' : 'Start a conversation'}
            </p>
            <p className="text-[var(--text-tertiary)] text-xs max-w-[240px]">
              {!agentId
                ? 'Choose an agent from the sidebar to begin your interaction'
                : 'Send a message to interact with the AI agent'}
            </p>
          </div>
        )}

        {/* Bootstrap greeting (static, shown before first real exchange) */}
        {showBootstrapGreeting && (
          <div className="animate-slide-up">
            <MessageBubble
              message={{
                id: 'bootstrap-greeting',
                role: 'assistant',
                content: BOOTSTRAP_GREETING,
                timestamp: Date.now(),
              }}
            />
          </div>
        )}

        {/* History messages */}
        {historyMessages.length > 0 && (
          <>
            {historyMessages.map((message, index) => (
              <div
                key={`history-${index}`}
                className="opacity-70"
              >
                <MessageBubble
                  message={{
                    id: `history-${index}`,
                    role: message.role,
                    content: message.content,
                    timestamp: message.timestamp ? new Date(message.timestamp).getTime() : Date.now(),
                  }}
                />
              </div>
            ))}

            {/* History divider */}
            <div className="flex items-center gap-3 py-2">
              <div className="flex-1 h-px bg-gradient-to-r from-transparent via-[var(--border-default)] to-transparent" />
              <div className="flex items-center gap-1.5 px-3 py-1 rounded-full bg-[var(--bg-tertiary)] border border-[var(--border-subtle)]">
                <History className="w-3 h-3 text-[var(--text-tertiary)]" />
                <span className="text-[10px] text-[var(--text-tertiary)] font-medium uppercase tracking-wider">
                  History Above
                </span>
              </div>
              <div className="flex-1 h-px bg-gradient-to-r from-transparent via-[var(--border-default)] to-transparent" />
            </div>
          </>
        )}

        {/* Current session messages */}
        {messages.map((message, index) => (
          <div
            key={message.id}
            className="animate-slide-up"
            style={{ animationDelay: `${Math.min(index * 50, 200)}ms` }}
          >
            <MessageBubble message={message} />
          </div>
        ))}

        {/* Streaming assistant message */}
        {isStreaming && getUserVisibleResponse() && (
          <div className="animate-fade-in">
            <MessageBubble
              message={{
                id: 'streaming',
                role: 'assistant',
                content: getUserVisibleResponse()!,
                timestamp: Date.now(),
              }}
              isStreaming
            />
          </div>
        )}

        {/* Loading indicator / Live activity preview */}
        {isStreaming && !getUserVisibleResponse() && (() => {
          // Derive friendly status from the latest progress step (steps 0→3.3)
          const getInitStatus = () => {
            if (currentSteps.length === 0) return 'Starting up...';
            const latestStep = currentSteps[currentSteps.length - 1];
            const s = latestStep.step;
            if (s === '0') return 'Initializing...';
            if (s === '1') return 'Loading context...';
            if (s === '2') return 'Loading resources...';
            if (s === '2.5') return 'Preparing workspace...';
            if (s === '3' && !currentSteps.some(st => st.step.startsWith('3.4'))) return 'Building context...';
            return 'Thinking...';
          };

          // Agent loop tool call steps (3.4.x), excluding send_message_to_user_directly
          const toolSteps = currentSteps.filter(
            (s) => /^3\.4\.\d+$/.test(s.step) &&
              !(s.details && typeof s.details === 'object' && typeof (s.details as Record<string, unknown>).tool_name === 'string' &&
                ((s.details as Record<string, unknown>).tool_name as string).endsWith('send_message_to_user_directly'))
          );

          const hasThinking = !!(currentThinking || currentAssistantMessage);
          const hasActivity = hasThinking || toolSteps.length > 0;

          return (
            <div className="animate-fade-in p-4">
              {hasActivity ? (
                <div className="flex gap-3">
                  <div className="relative shrink-0 mt-1">
                    <Loader2 className="w-4 h-4 text-[var(--accent-primary)] animate-spin" />
                    <div className="absolute inset-0 bg-[var(--accent-primary)] blur-md opacity-30" />
                  </div>
                  <div
                    className="flex-1 overflow-y-auto space-y-2"
                    style={{ maxHeight: '200px' }}
                  >
                    {/* Thinking text */}
                    {hasThinking && (
                      <div className="text-sm italic text-[var(--text-tertiary)] whitespace-pre-wrap leading-relaxed">
                        {currentThinking || currentAssistantMessage}
                      </div>
                    )}
                    {/* Inline tool call steps */}
                    {toolSteps.map((step) => (
                      <div
                        key={step.id}
                        className="flex items-center gap-2 text-xs text-[var(--text-secondary)] py-1 px-2 rounded bg-[var(--bg-tertiary)]/50 border border-[var(--border-subtle)]"
                      >
                        {step.status === 'completed' ? (
                          <CheckCircle2 className="w-3.5 h-3.5 text-[var(--color-success)] shrink-0" />
                        ) : (
                          <Loader2 className="w-3.5 h-3.5 text-[var(--accent-primary)] animate-spin shrink-0" />
                        )}
                        <span className="font-medium truncate">{step.title}</span>
                        {step.description && (
                          <span className="text-[var(--text-tertiary)] truncate hidden sm:inline">
                            {step.description.length > 60 ? step.description.slice(0, 60) + '...' : step.description}
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="flex items-center gap-3">
                  <div className="relative">
                    <Loader2 className="w-5 h-5 text-[var(--accent-primary)] animate-spin" />
                    <div className="absolute inset-0 bg-[var(--accent-primary)] blur-md opacity-30" />
                  </div>
                  <span className="text-sm text-[var(--text-secondary)]">{getInitStatus()}</span>
                </div>
              )}
            </div>
          );
        })()}

        <div ref={messagesEndRef} />
      </div>

      {/* Input area — only locked when the CURRENT agent is streaming */}
      <div className="p-4 border-t border-[var(--border-subtle)] bg-[var(--bg-secondary)]/20">
        <div className="flex gap-3 items-end">
          <div className="flex-1 relative">
            <Textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              onCompositionStart={handleCompositionStart}
              onCompositionUpdate={handleCompositionUpdate}
              onCompositionEnd={handleCompositionEnd}
              placeholder={!agentId ? 'Select an agent first...' : 'Type your message...'}
              disabled={isLoading || !agentId}
              className={cn(
                'min-h-[52px] max-h-[160px] pr-4 resize-none',
                'text-[var(--text-primary)]',
                isLoading && 'opacity-60'
              )}
              rows={1}
            />
          </div>
          <Button
            variant="accent"
            size="icon"
            onClick={handleSubmit}
            disabled={!input.trim() || isLoading || !agentId}
            className={cn(
              'shrink-0 h-[52px] w-[52px] rounded-xl',
              isLoading && 'animate-pulse'
            )}
          >
            {isLoading ? (
              <Loader2 className="w-5 h-5 animate-spin" />
            ) : (
              <Send className="w-5 h-5" />
            )}
          </Button>
        </div>

        {/* Keyboard hint */}
        <p className="mt-2 text-[10px] text-[var(--text-tertiary)] text-center">
          Press <kbd className="px-1.5 py-0.5 rounded bg-[var(--bg-tertiary)] border border-[var(--border-default)] font-mono text-[9px]">Enter</kbd> to send, <kbd className="px-1.5 py-0.5 rounded bg-[var(--bg-tertiary)] border border-[var(--border-default)] font-mono text-[9px]">Shift + Enter</kbd> for new line
        </p>
      </div>
    </Card>
  );
}
