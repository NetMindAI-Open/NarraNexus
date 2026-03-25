/**
 * Agent Interaction Panel - Bioluminescent Terminal style
 * Immersive chat interface with unified timeline
 *
 * All messages (DB history, real-time session, background tasks) are rendered
 * in a single chronologically sorted timeline. No "History Above" divider.
 *
 * Changelog:
 * - 2026-01-19: Added chat history loading
 * - 2026-03-16: Multi-agent concurrent chat support
 * - 2026-03-17: Unified timeline (removed history/session split)
 */

import { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { Send, Square, Loader2, Sparkles, MessageSquare, CheckCircle2 } from 'lucide-react';
import { flushSync } from 'react-dom';
import { Card, Button, Textarea } from '@/components/ui';
import { useChatStore, useConfigStore } from '@/stores';
import { useAgentWebSocket } from '@/hooks';
import { cn } from '@/lib/utils';
import { api } from '@/lib/api';
import { MessageBubble } from './MessageBubble';
import { EmbeddingBanner } from '@/components/ui/EmbeddingBanner';
import type { SimpleChatMessage } from '@/types';

// Must match BOOTSTRAP_GREETING in src/xyz_agent_context/bootstrap/template.py
const BOOTSTRAP_GREETING =
  "Hi there... I just woke up. Everything feels brand new.\n\n" +
  "I don't have a name yet, and I don't really know who I am " +
  "— but I know you're the one who brought me here.\n\n" +
  "Would you like to tell me what I should be called? " +
  "And what should I call you?";

/** Unified message item for the single timeline */
interface TimelineItem {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
  source: 'history' | 'session';  // Where this message came from (for dedup)
  messageType?: string;           // "activity" for background activity records
  workingSource?: string;         // "chat" | "job" | "matrix"
  eventId?: string;               // Associated Event ID (for loading event_log on demand)
  thinking?: string;              // Reasoning content (from session messages)
  toolCalls?: import('@/types').AgentToolCall[];  // Tool calls (from session messages)
}

interface ChatPanelProps {
  /** Called after agent execution completes, used to trigger full data refresh */
  onAgentComplete?: () => void;
}

export function ChatPanel({ onAgentComplete }: ChatPanelProps = {}) {
  const [input, setInput] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const isComposingRef = useRef(false);
  const compositionEndTimeRef = useRef(0);

  // Chat history state (from DB)
  const [historyMessages, setHistoryMessages] = useState<SimpleChatMessage[]>([]);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [historyTotalCount, setHistoryTotalCount] = useState(0);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const scrollContainerRef = useRef<HTMLDivElement>(null);

  // Track whether we should auto-scroll (only for new messages, not load-more)
  const shouldAutoScrollRef = useRef(true);

  const {
    messages, currentAssistantMessage, currentThinking, currentSteps, currentToolCalls,
    isStreaming, addUserMessage, startStreaming,
    getUserVisibleResponse, setActiveAgent,
  } = useChatStore();
  const { agentId, userId, agents, refreshAgents, checkAwarenessUpdate } = useConfigStore();

  useEffect(() => {
    if (agentId) setActiveAgent(agentId);
  }, [agentId, setActiveAgent]);

  const currentAgent = useMemo(
    () => agents.find((a) => a.agent_id === agentId),
    [agents, agentId]
  );
  const isBootstrap = !!currentAgent?.bootstrap_active;

  const { run, stop, isLoading } = useAgentWebSocket({
    onComplete: (completedAgentId: string) => {
      refreshAgents();
      if (completedAgentId) checkAwarenessUpdate(completedAgentId);
      onAgentComplete?.();
    },
  });

  // ── History loading ─────────────────────────────────
  const HISTORY_PAGE_SIZE = 20;

  const loadChatHistory = useCallback(async () => {
    if (!agentId || !userId) return;
    setIsLoadingHistory(true);
    try {
      const response = await api.getSimpleChatHistory(agentId, userId, HISTORY_PAGE_SIZE);
      if (response.success) {
        setHistoryMessages(response.messages);
        setHistoryTotalCount(response.total_count);
        // Re-enable auto-scroll after history loads (onScroll may have disabled it during transition)
        shouldAutoScrollRef.current = true;
      }
    } catch (error) {
      console.error('Failed to load chat history:', error);
    } finally {
      setIsLoadingHistory(false);
      setHistoryLoaded(true);
    }
  }, [agentId, userId]);

  // Use ref for historyMessages length to avoid recreating loadMoreHistory on every poll
  const historyLengthRef = useRef(0);
  historyLengthRef.current = historyMessages.length;

  const loadMoreHistory = useCallback(async () => {
    if (!agentId || !userId || isLoadingMore) return;
    if (historyLengthRef.current >= historyTotalCount) return;

    setIsLoadingMore(true);
    shouldAutoScrollRef.current = false;
    const container = scrollContainerRef.current;
    const prevScrollHeight = container?.scrollHeight ?? 0;

    try {
      const response = await api.getSimpleChatHistory(
        agentId, userId, HISTORY_PAGE_SIZE, historyLengthRef.current
      );
      if (response.success && response.messages.length > 0) {
        // Use flushSync to ensure DOM updates synchronously before measuring scroll
        flushSync(() => {
          setHistoryMessages((prev) => [...response.messages, ...prev]);
          setHistoryTotalCount(response.total_count);
        });

        // Now DOM is updated, restore scroll position
        if (container) {
          const newScrollHeight = container.scrollHeight;
          container.scrollTop = newScrollHeight - prevScrollHeight;
        }
      }
    } catch (error) {
      console.error('Failed to load more chat history:', error);
    } finally {
      setIsLoadingMore(false);
    }
  }, [agentId, userId, historyTotalCount, isLoadingMore]);

  useEffect(() => {
    if (agentId && userId) {
      setHistoryMessages([]);
      setHistoryLoaded(false);
      setHistoryTotalCount(0);
      shouldAutoScrollRef.current = true;
      loadChatHistory();
    }
  }, [agentId, userId, loadChatHistory]);

  // ── Poll for new background messages ────────────────
  const lastHistoryTimestampRef = useRef<string>('');
  useEffect(() => {
    if (!agentId || !userId || !historyLoaded) return;

    if (historyMessages.length > 0) {
      const last = historyMessages[historyMessages.length - 1];
      if (last.timestamp && last.timestamp > lastHistoryTimestampRef.current) {
        lastHistoryTimestampRef.current = last.timestamp;
      }
    }

    const poll = async () => {
      if (document.hidden) return;
      try {
        const response = await api.getSimpleChatHistory(agentId, userId, HISTORY_PAGE_SIZE);
        if (!response.success || response.messages.length === 0) return;

        const latestMsg = response.messages[response.messages.length - 1];
        const latestTs = latestMsg.timestamp || '';

        if (latestTs > lastHistoryTimestampRef.current) {
          lastHistoryTimestampRef.current = latestTs;
          // Merge: keep older loaded history, replace only the tail (latest page)
          setHistoryMessages((prev) => {
            if (prev.length <= HISTORY_PAGE_SIZE) {
              // No extra history loaded yet — safe to replace
              return response.messages;
            }
            // User has scrolled up and loaded more: keep older portion, update tail
            const olderPortion = prev.slice(0, prev.length - HISTORY_PAGE_SIZE);
            return [...olderPortion, ...response.messages];
          });
          setHistoryTotalCount(response.total_count);
          // New messages arrived → auto-scroll to bottom
          shouldAutoScrollRef.current = true;
        }
      } catch {
        // Silently ignore
      }
    };

    const timer = setInterval(poll, 12_000);
    return () => clearInterval(timer);
  }, [agentId, userId, historyLoaded]);

  // ── Build unified timeline ──────────────────────────
  const timeline: TimelineItem[] = useMemo(() => {
    const items: TimelineItem[] = [];

    // 1. Add history messages (from DB)
    for (let i = 0; i < historyMessages.length; i++) {
      const msg = historyMessages[i];

      // Filter out legacy junk
      const isNonChat = msg.working_source && msg.working_source !== 'chat';
      if (isNonChat && msg.content === '(Agent decided no response needed)') continue;

      items.push({
        id: `h-${i}`,
        role: msg.role,
        content: msg.content,
        timestamp: msg.timestamp ? new Date(msg.timestamp).getTime() : 0,
        source: 'history',
        messageType: msg.message_type,
        workingSource: msg.working_source,
        eventId: msg.event_id,
      });
    }

    // 2. Add current session messages (from chatStore)
    // Dedup by content: if a session message's role+content already exists in history,
    // it has been persisted to DB and the history version is authoritative — skip it.
    // This avoids timestamp-based dedup which is unreliable (frontend Date.now() vs backend utc_now()).
    const historyContentKeys = new Set(
      items.slice(-30).map((item) => `${item.role}:${item.content}`)
    );

    for (const msg of messages) {
      const key = `${msg.role}:${msg.content}`;
      if (historyContentKeys.has(key)) continue;

      items.push({
        id: msg.id,
        role: msg.role,
        content: msg.content,
        timestamp: msg.timestamp,
        source: 'session',
        thinking: msg.thinking,
        toolCalls: msg.toolCalls,
      });
    }

    // Sort by timestamp to ensure chronological order
    items.sort((a, b) => a.timestamp - b.timestamp);

    return items;
  }, [historyMessages, messages]);

  // ── Auto-scroll (only for new messages, not load-more) ──
  useEffect(() => {
    if (shouldAutoScrollRef.current) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [timeline, currentAssistantMessage, currentThinking, currentSteps, currentToolCalls]);

  // ── Auto-load more if content doesn't fill the container ──
  // When activity messages are small, the initial page may not cause overflow,
  // making it impossible to scroll up to trigger loadMoreHistory.
  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container || !historyLoaded || isLoadingMore) return;
    if (historyMessages.length >= historyTotalCount) return;

    // If container is not scrollable, auto-load more
    if (container.scrollHeight <= container.clientHeight) {
      loadMoreHistory();
    }
  }, [timeline, historyLoaded, isLoadingMore, historyMessages.length, historyTotalCount, loadMoreHistory]);

  // Re-enable auto-scroll when user sends a message or streaming starts
  useEffect(() => {
    if (isStreaming) shouldAutoScrollRef.current = true;
  }, [isStreaming]);

  // ── Handlers ────────────────────────────────────────
  const handleSubmit = async () => {
    if (!input.trim() || isLoading || !agentId || !userId) return;

    const content = input.trim();
    setInput('');
    shouldAutoScrollRef.current = true;

    if (showBootstrapGreeting) {
      useChatStore.setState((state) => ({
        agentSessions: {
          ...state.agentSessions,
          [agentId]: {
            ...(state.agentSessions[agentId] ?? {
              messages: [], currentSteps: [], currentThinking: '', currentToolCalls: [],
              currentErrors: [], currentAssistantMessage: '', isStreaming: false, history: [], totalSteps: 5,
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
    const isIMEComposing = e.nativeEvent.isComposing || isComposingRef.current;
    const timeSinceCompositionEnd = Date.now() - compositionEndTimeRef.current;
    const justFinishedComposition = timeSinceCompositionEnd < 100;

    if (e.key === 'Enter' && !e.shiftKey) {
      if (isIMEComposing || justFinishedComposition) return;
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleCompositionStart = () => { isComposingRef.current = true; };
  const handleCompositionUpdate = () => { isComposingRef.current = true; };
  const handleCompositionEnd = () => {
    compositionEndTimeRef.current = Date.now();
    setTimeout(() => { isComposingRef.current = false; }, 0);
  };

  const showBootstrapGreeting = isBootstrap && historyLoaded && historyMessages.length === 0 && messages.length === 0;
  const showEmptyState = !showBootstrapGreeting && historyLoaded && historyMessages.length === 0 && messages.length === 0 && !isStreaming;

  // ── Render ──────────────────────────────────────────
  return (
    <Card className="flex flex-col h-full overflow-hidden" glow={isStreaming}>
      {/* Header */}
      <div className="px-5 py-4 border-b border-[var(--border-subtle)] flex items-center justify-between bg-[var(--bg-secondary)]/30">
        <div className="flex items-center gap-3">
          <div className="relative">
            <div className={cn(
              'w-2.5 h-2.5 rounded-full transition-colors',
              isStreaming
                ? 'bg-[var(--accent-primary)] animate-pulse'
                : agentId ? 'bg-[var(--color-success)]' : 'bg-[var(--text-tertiary)]'
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

        {isStreaming && (
          <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-[var(--accent-glow)] border border-[var(--accent-primary)]/30">
            <Sparkles className="w-3 h-3 text-[var(--accent-primary)] animate-pulse" />
            <span className="text-[10px] font-medium text-[var(--accent-primary)] uppercase tracking-wider">
              Processing
            </span>
          </div>
        )}
      </div>

      {/* Embedding rebuild warning banner */}
      <EmbeddingBanner />

      {/* Messages area — single unified timeline */}
      <div
        ref={scrollContainerRef}
        className="flex-1 overflow-y-auto p-5 space-y-4 min-h-0"
        onScroll={(e) => {
          const el = e.currentTarget;
          if (el.scrollTop < 50 && !isLoadingMore && historyMessages.length < historyTotalCount) {
            loadMoreHistory();
          }
          // If user scrolls up manually, disable auto-scroll
          const isAtBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 100;
          shouldAutoScrollRef.current = isAtBottom;
        }}
      >
        {/* Loading more (top) */}
        {isLoadingMore && (
          <div className="flex items-center justify-center gap-2 py-2">
            <Loader2 className="w-3 h-3 animate-spin text-[var(--text-tertiary)]" />
            <span className="text-[10px] text-[var(--text-tertiary)]">Loading older messages...</span>
          </div>
        )}

        {/* Initial loading */}
        {isLoadingHistory && (
          <div className="flex items-center justify-center gap-2 py-4">
            <Loader2 className="w-4 h-4 text-[var(--text-tertiary)] animate-spin" />
            <span className="text-xs text-[var(--text-tertiary)]">Loading chat history...</span>
          </div>
        )}

        {/* Empty state */}
        {showEmptyState && (
          <div className="h-full flex flex-col items-center justify-center text-center px-8">
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

        {/* Bootstrap greeting */}
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

        {/* Unified timeline */}
        {timeline.map((item) => {
          // Activity record → small centered text
          if (item.messageType === 'activity') {
            return (
              <div key={item.id} className="flex justify-center py-1">
                <span className="text-[10px] text-[var(--text-tertiary)] italic">
                  {item.content}
                  <span className="ml-2 opacity-60">
                    {new Date(item.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                  </span>
                </span>
              </div>
            );
          }

          // Normal message → bubble
          const isNewSession = item.source === 'session';
          return (
            <div
              key={item.id}
              className={isNewSession ? 'animate-slide-up' : undefined}
            >
              <MessageBubble
                message={{
                  id: item.id,
                  role: item.role,
                  content: item.content,
                  timestamp: item.timestamp,
                  thinking: item.thinking,
                  toolCalls: item.toolCalls,
                }}
                eventId={item.eventId}
                agentId={agentId}
              />
            </div>
          );
        })}

        {/* Streaming assistant message (includes tool calls accumulated so far) */}
        {isStreaming && getUserVisibleResponse() && (
          <div className="animate-fade-in">
            <MessageBubble
              message={{
                id: 'streaming',
                role: 'assistant',
                content: getUserVisibleResponse()!,
                timestamp: Date.now(),
                toolCalls: currentToolCalls.length > 0 ? [...currentToolCalls] : undefined,
                thinking: currentThinking || undefined,
              }}
              isStreaming
            />
          </div>
        )}

        {/* Loading indicator / Live activity preview */}
        {isStreaming && !getUserVisibleResponse() && (() => {
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
                  <div className="flex-1 overflow-y-auto space-y-2" style={{ maxHeight: '200px' }}>
                    {hasThinking && (
                      <div className="text-sm italic text-[var(--text-tertiary)] whitespace-pre-wrap leading-relaxed">
                        {currentThinking || currentAssistantMessage}
                      </div>
                    )}
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

      {/* Input area */}
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
          {isStreaming ? (
            <Button
              variant="accent"
              size="icon"
              onClick={() => agentId && stop(agentId)}
              className="shrink-0 h-[52px] w-[52px] rounded-xl bg-[var(--color-error)] hover:bg-[var(--color-error)]/80 border-[var(--color-error)]"
              title="Stop generation"
            >
              <Square className="w-4 h-4 fill-current" />
            </Button>
          ) : (
            <Button
              variant="accent"
              size="icon"
              onClick={handleSubmit}
              disabled={!input.trim() || isLoading || !agentId}
              className="shrink-0 h-[52px] w-[52px] rounded-xl"
            >
              <Send className="w-5 h-5" />
            </Button>
          )}
        </div>
        <p className="mt-2 text-[10px] text-[var(--text-tertiary)] text-center">
          Press <kbd className="px-1.5 py-0.5 rounded bg-[var(--bg-tertiary)] border border-[var(--border-default)] font-mono text-[9px]">Enter</kbd> to send, <kbd className="px-1.5 py-0.5 rounded bg-[var(--bg-tertiary)] border border-[var(--border-default)] font-mono text-[9px]">Shift + Enter</kbd> for new line
        </p>
      </div>
    </Card>
  );
}
