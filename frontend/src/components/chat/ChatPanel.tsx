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
import { Send, Square, Loader2, Sparkles, MessageSquare, CheckCircle2, Paperclip, X, FileText, Image as ImageIcon } from 'lucide-react';
import { flushSync } from 'react-dom';
import { Card, Button, Textarea, ScrollArea } from '@/components/ui';
import { useChatStore, useConfigStore } from '@/stores';
import { useAgentWebSocket } from '@/hooks';
import { cn } from '@/lib/utils';
import { api } from '@/lib/api';
import { MessageBubble } from './MessageBubble';
import { AttachmentImage } from './AttachmentImage';
import { EmbeddingBanner } from '@/components/ui/EmbeddingBanner';
import type { Attachment, SimpleChatMessage } from '@/types';

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
  workingSource?: string;         // "chat" | "job" | "lark"
  eventId?: string;               // Associated Event ID (for loading event_log on demand)
  thinking?: string;              // Reasoning content (from session messages)
  toolCalls?: import('@/types').AgentToolCall[];  // Tool calls (from session messages)
  attachments?: Attachment[];     // User-uploaded files referenced by this message
}

interface ChatPanelProps {
  /** Called after agent execution completes, used to trigger full data refresh */
  onAgentComplete?: () => void;
}

export function ChatPanel({ onAgentComplete }: ChatPanelProps = {}) {
  const [input, setInput] = useState('');
  // Attachments uploaded for the next message but not yet sent. Each entry
  // is the server-acknowledged metadata returned by uploadAttachment.
  const [pendingAttachments, setPendingAttachments] = useState<Attachment[]>([]);
  // Tracks how many uploads are in-flight so the send button can wait.
  const [uploadingCount, setUploadingCount] = useState(0);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
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

  // Bug 15: initial open (or agent switch) must land at the very bottom
  // instantly, *after* MessageBubble subtrees (markdown, code blocks,
  // tool-call UI) have had a frame to lay out. A smooth scrollIntoView
  // from mount-time position can't catch a container that keeps growing
  // as async content renders. We raise this flag whenever fresh history
  // is loaded and consume it in a dedicated rAF-gated effect below.
  const initialScrollPendingRef = useRef(false);

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
        // Bug 15: request an instant jump-to-bottom once timeline has
        // rendered. The dedicated rAF-gated effect picks this up.
        initialScrollPendingRef.current = true;
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
          // New messages arrived → auto-scroll to bottom.
          // Bug 15: route through initialScrollPendingRef so the
          // instant-jump effect handles it (smooth scrollIntoView lost
          // the race against async markdown layout).
          shouldAutoScrollRef.current = true;
          initialScrollPendingRef.current = true;
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
        attachments: msg.attachments,
      });
    }

    // 2. Add current session messages (from chatStore)
    //
    // Dedup by (role + content) AND timestamp proximity: if a history entry
    // with identical role+content exists within SAME_MESSAGE_WINDOW_MS of the
    // session message's timestamp, they are the same message (already
    // persisted) — drop the session copy.
    //
    // Bug 19: the match MUST consume the history timestamp it pairs with,
    // otherwise a single history row can dedup multiple session messages of
    // the same role+content. Real-world trigger: user retries the exact
    // same question after a failed turn — session then has both the
    // original user message (which legitimately matches history) AND the
    // retry (which must NOT, because the history row belongs to the first
    // one). Without consumption, the retry disappears from the UI.
    //
    // The window is a safety net for browser/server clock skew. After the
    // backend fix that stamps user messages at turn-start (Event.created_at)
    // instead of turn-end (utc_now() after agent finishes), the real diff
    // between session ts and history ts is just RTT — milliseconds. The
    // window only needs to cover clock drift now:
    //   - NTP-synced machine: < 1s drift (any window works)
    //   - Laptop off-network a while: 10s–1min
    //   - Neglected / post-sleep laptop: can hit a few minutes
    // 5 min covers realistic drift without being so loose that repeat-text
    // edge cases feel weird. Note: short identical content sent twice
    // (e.g. "好" / "go on") is NOT a false-positive source — the
    // "consume matched history timestamp" logic pairs them one-to-one.
    const SAME_MESSAGE_WINDOW_MS = 300_000;
    const historyByKey = new Map<string, number[]>();
    for (const item of items) {
      const key = `${item.role}:${item.content}`;
      const list = historyByKey.get(key);
      if (list) {
        list.push(item.timestamp);
      } else {
        historyByKey.set(key, [item.timestamp]);
      }
    }

    for (const msg of messages) {
      const key = `${msg.role}:${msg.content}`;
      const historyTimestamps = historyByKey.get(key);
      const matchIdx = historyTimestamps
        ? historyTimestamps.findIndex(
            (ts) => Math.abs(msg.timestamp - ts) < SAME_MESSAGE_WINDOW_MS,
          )
        : -1;
      if (matchIdx >= 0 && historyTimestamps) {
        // Consume the matched history timestamp so the next session
        // message of the same role+content doesn't pair against it.
        historyTimestamps.splice(matchIdx, 1);
        continue;
      }

      items.push({
        id: msg.id,
        role: msg.role,
        content: msg.content,
        timestamp: msg.timestamp,
        source: 'session',
        thinking: msg.thinking,
        toolCalls: msg.toolCalls,
        attachments: msg.attachments,
      });
    }

    // Sort by timestamp, with id as tie-breaker so same-ms messages are still
    // totally ordered (Array.sort is spec-stable but the input order can be
    // wrong when history and session are interleaved).
    items.sort((a, b) => {
      if (a.timestamp !== b.timestamp) return a.timestamp - b.timestamp;
      return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
    });

    return items;
  }, [historyMessages, messages]);

  // ── Bug 15: initial jump-to-bottom on open / agent switch ──
  //
  // After fresh history loads, wait one animation frame for MessageBubble
  // subtrees (markdown, code highlighting, tool-call UI) to settle, then
  // snap the chat container straight to the bottom. We operate on
  // scrollContainerRef directly (not scrollIntoView on a sentinel) so
  // we don't accidentally scroll ancestor containers. behavior is
  // instant — smooth animation from the top can't catch a container
  // that keeps growing as async content renders below the animation.
  useEffect(() => {
    if (!initialScrollPendingRef.current) return;
    if (timeline.length === 0) return;
    const container = scrollContainerRef.current;
    if (!container) return;

    let cancelled = false;
    const id = requestAnimationFrame(() => {
      if (cancelled) return;
      container.scrollTop = container.scrollHeight;
      initialScrollPendingRef.current = false;
    });
    return () => {
      cancelled = true;
      cancelAnimationFrame(id);
    };
  }, [timeline]);

  // ── Streaming auto-scroll ──
  //
  // During streaming, each delta adds a small amount of content; a smooth
  // scrollIntoView per update gives the nice "following along" feel.
  // Gated by isStreaming so it does NOT fire on initial open (that path
  // is handled by the instant-jump effect above).
  useEffect(() => {
    if (!isStreaming) return;
    if (!shouldAutoScrollRef.current) return;
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [isStreaming, currentAssistantMessage, currentThinking, currentSteps, currentToolCalls]);

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

  // ── Attachment handlers ──────────────────────────────
  const uploadAttachments = useCallback(
    async (files: File[]) => {
      if (!agentId || !userId || files.length === 0) return;
      setUploadingCount((n) => n + files.length);
      for (const file of files) {
        try {
          const resp = await api.uploadAttachment(agentId, userId, file);
          if (resp.success && resp.file_id && resp.mime_type && resp.category) {
            setPendingAttachments((prev) => [
              ...prev,
              {
                file_id: resp.file_id!,
                mime_type: resp.mime_type!,
                original_name: resp.original_name ?? file.name,
                size_bytes: resp.size_bytes ?? file.size,
                category: resp.category!,
              },
            ]);
          } else {
            console.error('Attachment upload failed:', resp.error);
          }
        } catch (e) {
          console.error('Attachment upload error:', e);
        } finally {
          setUploadingCount((n) => Math.max(0, n - 1));
        }
      }
    },
    [agentId, userId],
  );

  const handleFilePick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files ? Array.from(e.target.files) : [];
    e.target.value = ''; // allow re-selecting the same file
    if (files.length) uploadAttachments(files);
  };

  const handleRemoveAttachment = (fileId: string) => {
    setPendingAttachments((prev) => prev.filter((a) => a.file_id !== fileId));
  };

  // Drag handlers are typed loosely (HTMLElement) because they're attached
  // to BOTH the outer wrapper div (visual highlight) AND the <Textarea>
  // itself (where the native default-text-insert lives). Both call sites
  // need preventDefault on dragover (to opt the element in as a drop
  // target) and on drop (to cancel the textarea's default).
  const handleDragOver = (e: React.DragEvent<HTMLElement>) => {
    if (!agentId) return;
    // Only treat the drag as an attachment intent if it actually carries
    // files — typing-style drags (selected text from another tab) should
    // still fall through to the textarea's normal text-paste behavior.
    const types = e.dataTransfer?.types;
    if (!types || !Array.from(types).includes('Files')) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
    setIsDragging(true);
  };
  const handleDragLeave = (e: React.DragEvent<HTMLElement>) => {
    e.preventDefault();
    // dragleave fires when the cursor crosses any child boundary, not just
    // when truly leaving the bound element. relatedTarget is the element
    // the cursor moved to — if it's still inside us, ignore.
    const related = e.relatedTarget as Node | null;
    if (related && e.currentTarget.contains(related)) return;
    setIsDragging(false);
  };
  const handleDrop = (e: React.DragEvent<HTMLElement>) => {
    if (!agentId) return;
    const files = Array.from(e.dataTransfer?.files || []);
    if (files.length === 0) return;
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    uploadAttachments(files);
  };

  const handlePaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    if (!agentId) return;
    // Walk clipboard items; collect anything the OS hands us as a File
    // (covers OS screenshot → image/png, "Copy image" from a browser, and
    // copying a file in the file manager). If the user just copied text,
    // there are no file-kind items and we fall through to default paste.
    const items = e.clipboardData?.items;
    if (!items) return;
    const files: File[] = [];
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (item.kind === 'file') {
        const f = item.getAsFile();
        if (f) files.push(f);
      }
    }
    if (files.length === 0) return;
    e.preventDefault();
    uploadAttachments(files);
  };

  // ── Handlers ────────────────────────────────────────
  const handleSubmit = async () => {
    const trimmed = input.trim();
    const hasContent = trimmed.length > 0 || pendingAttachments.length > 0;
    if (!hasContent || isLoading || !agentId || !userId || uploadingCount > 0) return;

    const content = trimmed;
    const attachmentsToSend = pendingAttachments;
    setInput('');
    setPendingAttachments([]);
    shouldAutoScrollRef.current = true;
    // Bug 15: snap to bottom for the user's freshly-sent bubble before
    // streaming starts. The streaming effect takes over from there.
    initialScrollPendingRef.current = true;

    if (showBootstrapGreeting) {
      useChatStore.setState((state) => ({
        agentSessions: {
          ...state.agentSessions,
          [agentId]: {
            ...(state.agentSessions[agentId] ?? {
              messages: [], currentSteps: [], currentThinking: '', currentToolCalls: [],
              currentErrors: [], currentAssistantMessage: '', isStreaming: false, history: [],
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

    addUserMessage(agentId, content, attachmentsToSend.length ? attachmentsToSend : undefined);
    startStreaming(agentId);

    try {
      const agentName = currentAgent?.name || agentId;
      run(agentId, userId, content, agentName, attachmentsToSend.length ? attachmentsToSend : undefined);
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
    <Card
      // Make the entire chat panel a drop target — users naturally drag
      // files anywhere in the conversation surface, not just the input
      // box. Native default-prevention still has to live on the textarea
      // itself (see onDragOver/onDrop there) because <textarea> processes
      // drop synchronously into its value before bubbling.
      className={cn(
        'flex flex-col h-full overflow-hidden transition-colors',
        isDragging && 'ring-2 ring-inset ring-[var(--accent-primary)]'
      )}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {/* Header — archive document caption */}
      <div className="px-5 flex items-center justify-between border-b border-[var(--rule)] min-h-[48px]">
        <div className="flex items-center gap-2.5 min-w-0">
          <span
            className={cn(
              'w-1.5 h-1.5 rounded-full allow-circle shrink-0 transition-colors',
              isStreaming
                ? 'bg-[var(--color-yellow-500)] animate-pulse'
                : agentId ? 'bg-[var(--color-green-500)]' : 'bg-[var(--text-tertiary)]'
            )}
          />
          <span className="text-[11px] font-[family-name:var(--font-mono)] uppercase tracking-[0.16em] text-[var(--text-primary)]">
            Interaction
          </span>
          <span className="text-[11px] font-[family-name:var(--font-mono)] text-[var(--text-tertiary)] truncate">
            · {agentId || 'no agent'}
          </span>
        </div>

        {isStreaming && (
          <span className="flex items-center gap-1.5 text-[10px] font-[family-name:var(--font-mono)] uppercase tracking-[0.14em] text-[var(--color-yellow-500)]">
            <Sparkles className="w-3 h-3 animate-pulse" />
            Processing
          </span>
        )}
      </div>

      {/* Embedding rebuild warning banner */}
      <EmbeddingBanner />

      {/* Messages area — single unified timeline.
          Wrapped in <ScrollArea> so the scrollbar is JS-rendered (Radix) and
          cannot be hijacked by macOS's "always show scrollbars" AppKit
          fallback that ignores ::-webkit-scrollbar. The viewport ref is
          forwarded so existing scroll logic (auto-scroll-to-bottom, history
          load on scroll-top, anchor preservation) reads/writes the SAME
          element it always did. */}
      <ScrollArea
        className="flex-1 min-h-0"
        viewportRef={scrollContainerRef}
        viewportClassName="p-5"
        onViewportScroll={(e) => {
          const el = e.currentTarget;
          if (el.scrollTop < 50 && !isLoadingMore && historyMessages.length < historyTotalCount) {
            loadMoreHistory();
          }
          // If user scrolls up manually, disable auto-scroll
          const isAtBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 100;
          shouldAutoScrollRef.current = isAtBottom;
        }}
      >
      <div className="space-y-4">
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
            <MessageSquare className="w-8 h-8 text-[var(--text-tertiary)] opacity-40 mb-4" />
            <p className="text-[var(--text-primary)] text-sm mb-1.5">
              {!agentId ? 'Select an agent to start' : 'Start a conversation'}
            </p>
            <p className="text-[var(--text-tertiary)] text-xs max-w-[260px] leading-relaxed">
              {!agentId
                ? 'Choose an agent from the sidebar to begin your interaction.'
                : 'Send a message to interact with the AI agent.'}
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
                  attachments: item.attachments,
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
                  <ScrollArea className="flex-1" style={{ maxHeight: '200px' }}>
                    <div className="space-y-2">
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
                  </ScrollArea>
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
      </ScrollArea>

      {/* Input area — drop is handled at the Card root, so this wrapper
          no longer needs its own onDragOver/onDragLeave/onDrop. */}
      <div className="px-5 py-4 border-t border-[var(--rule)]">
        {/* Pending attachments preview row */}
        {(pendingAttachments.length > 0 || uploadingCount > 0) && (
          <div className="mb-2.5 flex flex-wrap gap-2">
            {pendingAttachments.map((att) => {
              const isImage = att.category === 'image';
              const canPreview = isImage && !!agentId && !!userId;
              return (
                <div
                  key={att.file_id}
                  className="relative flex items-center gap-2 rounded-md border border-[var(--rule)] bg-[var(--bg-tertiary)]/60 pr-7 pl-1.5 py-1 max-w-[240px]"
                >
                  {canPreview ? (
                    <AttachmentImage
                      agentId={agentId!}
                      userId={userId!}
                      fileId={att.file_id}
                      alt={att.original_name}
                      className="w-9 h-9 rounded object-cover shrink-0"
                    />
                  ) : (
                    <div className="w-9 h-9 rounded bg-[var(--bg-secondary)] flex items-center justify-center shrink-0">
                      {isImage ? (
                        <ImageIcon className="w-4 h-4 text-[var(--text-tertiary)]" />
                      ) : (
                        <FileText className="w-4 h-4 text-[var(--text-tertiary)]" />
                      )}
                    </div>
                  )}
                  <div className="min-w-0 flex-1 leading-tight">
                    <div className="text-xs truncate">{att.original_name}</div>
                    <div className="text-[10px] text-[var(--text-tertiary)] font-[family-name:var(--font-mono)] uppercase tracking-[0.1em]">
                      {att.category} · {Math.max(1, Math.round(att.size_bytes / 1024))} KB
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => handleRemoveAttachment(att.file_id)}
                    className="absolute right-1 top-1 p-0.5 rounded hover:bg-[var(--bg-secondary)]"
                    title="Remove"
                  >
                    <X className="w-3 h-3 text-[var(--text-tertiary)]" />
                  </button>
                </div>
              );
            })}
            {uploadingCount > 0 && (
              <div className="flex items-center gap-1.5 px-2 py-1 rounded-md border border-dashed border-[var(--rule)] text-[10px] text-[var(--text-tertiary)] font-[family-name:var(--font-mono)] uppercase tracking-[0.1em]">
                <Loader2 className="w-3 h-3 animate-spin" />
                Uploading {uploadingCount}
              </div>
            )}
          </div>
        )}

        <div className="flex gap-2.5 items-stretch">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={handleFilePick}
          />
          <Button
            variant="ghost"
            size="icon"
            onClick={() => fileInputRef.current?.click()}
            disabled={!agentId || isLoading}
            className="shrink-0 h-[52px] w-[52px]"
            title="Attach file"
          >
            <Paperclip className="w-4 h-4" />
          </Button>
          <div className="flex-1 relative">
            <Textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              onCompositionStart={handleCompositionStart}
              onCompositionUpdate={handleCompositionUpdate}
              onCompositionEnd={handleCompositionEnd}
              // Drag handlers MUST live on the textarea itself, not just on
              // a parent. Otherwise the browser's native textarea behavior
              // (drop file → insert file path as text) wins, because the
              // <textarea> element processes the drop default before the
              // bubbled React event reaches the parent's preventDefault.
              // Same reasoning for onPaste — clipboard items with kind=file
              // (e.g. OS screenshot) need to be intercepted at the textarea
              // before the default text-paste path strips them.
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              onPaste={handlePaste}
              placeholder={
                !agentId
                  ? 'Select an agent first…'
                  : isDragging
                    ? 'Drop file to attach…'
                    : 'Type your message… (drag files here to attach)'
              }
              disabled={isLoading || !agentId}
              className={cn(
                // Auto-resizing textarea: min-h sets the empty-state height,
                // max-h caps growth. The Textarea component manages
                // `style.height` based on scrollHeight on every input.
                // Padding 14 + line-height 24 + padding 14 = 52px exactly,
                // matching the send-button height next to it.
                'min-h-[52px] max-h-[160px] py-[14px] leading-[24px] resize-none',
                isLoading && 'opacity-60'
              )}
              rows={1}
            />
          </div>
          {isStreaming ? (
            <Button
              variant="danger"
              size="icon"
              onClick={() => agentId && stop(agentId)}
              className="shrink-0 h-[52px] w-[52px]"
              title="Stop generation"
            >
              <Square className="w-4 h-4 fill-current" />
            </Button>
          ) : (
            <Button
              variant="accent"
              size="icon"
              onClick={handleSubmit}
              disabled={
                (!input.trim() && pendingAttachments.length === 0)
                || isLoading
                || !agentId
                || uploadingCount > 0
              }
              className="shrink-0 h-[52px] w-[52px]"
              title="Send"
            >
              <Send className="w-4 h-4" />
            </Button>
          )}
        </div>
        <p className="mt-2 text-[10px] text-[var(--text-tertiary)] font-[family-name:var(--font-mono)] uppercase tracking-[0.12em] text-center">
          <kbd className="font-[family-name:var(--font-mono)]">Enter</kbd> to send
          <span className="opacity-40 mx-2">·</span>
          <kbd className="font-[family-name:var(--font-mono)]">Shift + Enter</kbd> new line
          <span className="opacity-40 mx-2">·</span>
          <kbd className="font-[family-name:var(--font-mono)]">Drop</kbd> to attach
        </p>
      </div>
    </Card>
  );
}
