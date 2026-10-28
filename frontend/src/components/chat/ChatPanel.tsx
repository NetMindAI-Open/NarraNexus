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

import { useState, useRef, useEffect, useCallback, useMemo, memo, useDeferredValue } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { CornerDownLeft, Square, Loader2, Plus, X, FileText, Image as ImageIcon, Mic } from 'lucide-react';
import { flushSync } from 'react-dom';
import { Card, Button, ScrollArea } from '@/components/ui';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/Dialog';
import { BracketEmptyState, BracketLoading } from '@/components/nm';
import { OnboardingJourney } from './OnboardingJourney';
import { ChatHeader } from './ChatHeader';
import { ComposerModelBadge } from './ComposerModelBadge';
import { ComposerFastToggle } from './ComposerFastToggle';
import { AgentLlmConfigPanel } from './AgentLlmConfigPanel';
import { useChatStore, useConfigStore, useArtifactStore } from '@/stores';
import { useAgentWebSocket, useFastMode } from '@/hooks';
import { cn, generateId } from '@/lib/utils';
import { api } from '@/lib/api';
import { localizeBootstrapGreeting as localizeBootstrapGreetingWith } from '@/lib/bootstrapGreeting';
import { buildUnifiedTimeline, type TimelineItem } from '@/lib/buildTimeline';
import { streamForTab } from '@/lib/chatStreams';
import { chatDayInfo } from '@/lib/chatDays';
import { capturePrependAnchor, restorePrependAnchor } from '@/lib/scrollAnchor';
import { getChatDraft } from '@/lib/chatDrafts';
import { captureProductEvent } from '@/lib/productAnalytics';
import { MessageBubble } from './MessageBubble';
import { InnerThoughtCard } from './InnerThoughtCard';
import { RunPhases } from './process/RunPhases';
import { PlanStrip } from './process/PlanStrip';
import { SegmentedReply } from './SegmentedReply';
import ResumedRunChip from './ResumedRunChip';
import { segmentTurn } from '@/lib/segmentTurn';
import { useStudioTurn } from '@/hooks/useStudioTurn';
import { Composer, type ComposerHandle } from './Composer';
import { AttachmentImage } from './AttachmentImage';
import { VoiceTranscript } from './VoiceTranscript';
import { AudioRecorder } from './AudioRecorder';
import { ArtifactInlineBadge } from '@/components/artifacts';
import type { Attachment, SimpleChatMessage, AgentToolCall } from '@/types';

// Artifact tool names that produce an artifact_id in tool_output.
//
// DISPLAY-ONLY since 2026-08-18 (spec artifact-events §3.3): discovery moved
// to backend-pushed `artifact_changed` events (useRunObservation → store's
// applyEvent), so this matching no longer feeds the artifact panel. It only
// anchors the inline badge chip to the tool call that produced the artifact —
// a rename here costs a missing chip in old transcripts, never a missing tab.
//
// MCP tools arrive in the stream fully-qualified — `mcp__<server>__<tool>`
// (e.g. `mcp__common_tools_module__register_artifact`), NOT the bare name,
// so match the bare suffix.
const ARTIFACT_TOOL_BASE_NAMES = ['register_artifact'];

function isArtifactToolName(toolName: string): boolean {
  return ARTIFACT_TOOL_BASE_NAMES.some(
    (base) => toolName === base || toolName.endsWith(`__${base}`),
  );
}

/**
 * Renders one-line ArtifactInlineBadge chips for any artifact-producing
 * tool calls in `toolCalls`.
 *
 * History: this used to render full ArtifactPreviewCard thumbnails (with
 * CSV head, image preview, etc). Users found them disruptive because each
 * re-register (refresh signal) re-mounted the card — the card visibly
 * flashed in/out and then evaporated on history reload (chat history
 * doesn't persist tool_calls). The badge is a one-line chip instead;
 * dedupe-by-artifact_id keeps a re-register from doubling the badges up.
 */
interface ArtifactToolCallCardsProps {
  toolCalls: AgentToolCall[];
  allArtifacts: ReturnType<typeof useArtifactStore.getState>['artifacts'];
}

const ArtifactToolCallCards = memo(function ArtifactToolCallCardsImpl({
  toolCalls, allArtifacts,
}: ArtifactToolCallCardsProps) {
  // Collect unique artifact_ids in first-seen order across the turn's tool
  // calls — PURELY for badge placement. The store is fed by backend
  // artifact_changed events (and the full pull on open/switch/reconnect);
  // this render path issues no fetches and mutates nothing, so it is safe
  // inside the timeline map and a parse miss costs a chip, never a tab.
  const seenIds = new Set<string>();
  const orderedIds: string[] = [];

  for (const tc of toolCalls) {
    if (!isArtifactToolName(tc.tool_name)) continue;
    if (!tc.tool_output) continue;

    let artifactId: string | undefined;
    try {
      const parsed = JSON.parse(tc.tool_output) as {
        artifact_id?: string;
      };
      artifactId = parsed.artifact_id;
    } catch {
      // tool_output is not JSON — skip
      continue;
    }

    if (!artifactId) continue;

    if (!seenIds.has(artifactId)) {
      seenIds.add(artifactId);
      orderedIds.push(artifactId);
    }
  }

  if (orderedIds.length === 0) return null;

  return (
    <div className="mt-1.5 flex flex-wrap gap-1.5">
      {orderedIds.map((id) => {
        const artifact = allArtifacts.find((a) => a.artifact_id === id);
        // Metadata may lag the tool_output by a roundtrip — render a muted
        // placeholder chip so layout doesn't jump when the artifact arrives.
        return artifact ? (
          <ArtifactInlineBadge key={id} artifact={artifact} />
        ) : (
          <span
            key={id}
            className="inline-flex items-center gap-1.5 px-2 py-0.5 text-[11px] font-[family-name:var(--font-mono)] border border-[var(--border-subtle)] text-[var(--text-tertiary)] opacity-60"
            title={`Loading artifact ${id}…`}
          >
            <span className="truncate">artifact…</span>
          </span>
        );
      })}
    </div>
  );
}, (prev, next) => {
  // Custom shallow compare so React.memo skips re-renders triggered by
  // unrelated keystrokes in the chat input. Each timeline item's `toolCalls`
  // array is built once via useMemo and stays referentially stable until the
  // streaming state actually advances, so the array identity check below is
  // sufficient. allArtifacts swaps when the artifact store updates (exactly
  // when we want to re-render to upgrade a placeholder chip to a real badge).
  return (
    prev.toolCalls === next.toolCalls &&
    prev.allArtifacts === next.allArtifacts
  );
});

// TimelineItem + the unified-timeline builder live in @/lib/buildTimeline
// (pure + unit-tested). ChatPanel just consumes the result.

interface ChatPanelProps {
  /** Called after agent execution completes, used to trigger full data refresh */
  onAgentComplete?: () => void;
}

export function ChatPanel({ onAgentComplete }: ChatPanelProps = {}) {
  const navigate = useNavigate();
  // The chat textarea + its per-agent draft live in <Composer>, isolated so a
  // keystroke re-renders only that small child — not this timeline-rendering
  // monolith (which also re-renders on every streaming delta). We read the
  // text imperatively on send (composerRef) and track only the empty↔non-empty
  // flip for the send button's disabled state (composerEmpty).
  const composerRef = useRef<ComposerHandle>(null);
  const [composerEmpty, setComposerEmpty] = useState(
    () => getChatDraft(useConfigStore.getState().agentId ?? '').trim().length === 0
  );
  // Attachments uploaded for the next message but not yet sent. Each entry
  // is the server-acknowledged metadata returned by uploadAttachment.
  const [pendingAttachments, setPendingAttachments] = useState<Attachment[]>([]);
  // Banner above the input when an audio upload comes back with
  // transcription_available=false. Cleared on next successful audio
  // upload or when user dismisses. Plain string so we don't need a
  // toast library — keeps the UI consistent with the rest of the panel.
  const [transcriptionNotice, setTranscriptionNotice] = useState<string | null>(null);
  // Click-time pre-flight for the mic button. ``undefined`` while the
  // first availability probe is in flight (button stays enabled — better
  // to false-positive once than block voice input on a network blip);
  // boolean once the probe lands. ``reason`` lets the unavailable
  // dialog vary its copy (free-tier vs. user-needs-to-configure).
  const [transcriptionAvailable, setTranscriptionAvailable] = useState<boolean | undefined>(undefined);
  const [transcriptionReason, setTranscriptionReason] = useState<string>('');
  const [voiceUnavailableDialogOpen, setVoiceUnavailableDialogOpen] = useState(false);
  // Per-agent model & framework panel, opened from the header (Owner-required
  // entry, reinstated 2026-09-11). A bump on save tells the composer model
  // chip to re-read the (possibly changed) model.
  const [agentCfgOpen, setAgentCfgOpen] = useState(false);
  const [modelReloadKey, setModelReloadKey] = useState(0);
  // Tracks how many uploads are in-flight so the send button can wait.
  const [uploadingCount, setUploadingCount] = useState(0);
  const [isDragging, setIsDragging] = useState(false);
  // Chat tabs: "Conversation" is the owner↔agent direct chat (original design);
  // "Inner Thoughts" holds the agent's background activity + cross-channel
  // narrations (turns whose working_source isn't chat/manyfold) so they aren't
  // shown as if the agent were speaking to the owner.
  const [chatTab, setChatTab] = useState<'conversation' | 'inner'>('conversation');
  // Declared up here (not next to the zero-state block below) because the
  // attachment intake handlers need it too: the Activity Log has no composer,
  // so a drag/paste there must not silently queue a file no surface can show.
  const isActivityTab = chatTab === 'inner';
  const fileInputRef = useRef<HTMLInputElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Chat history state (from DB). The conversation and the Activity Log are two
  // independently-paginated streams; each keeps its OWN rows / total / loaded
  // flag keyed by include, so switching tabs neither clears the other stream
  // nor refetches one already loaded (the active stream is `historyInclude`).
  const [historyByStream, setHistoryByStream] =
    useState<Record<'chat' | 'activity', SimpleChatMessage[]>>({ chat: [], activity: [] });
  const [loadedByStream, setLoadedByStream] =
    useState<Record<'chat' | 'activity', boolean>>({ chat: false, activity: false });
  const [totalByStream, setTotalByStream] =
    useState<Record<'chat' | 'activity', number>>({ chat: 0, activity: 0 });
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
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
    messages,
    currentAssistantMessage: _rtAssistantMessage,
    currentThinking: _rtThinking,
    currentSteps: _rtSteps,
    currentToolCalls: _rtToolCalls,
    currentEvents: _rtEvents,
    resumedRun,
    currentRunId,
    isStreaming, addUserMessage, addSteerMessage, markSteerRejected, startStreaming,
    currentSteerable,
    setActiveAgent,
  } = useChatStore();

  // Render-rate throttle (iron rule #16). The five streaming values update
  // on EVERY delta during a streaming storm; wrapping them in React 18
  // useDeferredValue lets React coalesce render bursts into fewer commits
  // when the main thread is busy, while ALWAYS converging to the latest
  // value — nothing is dropped or reordered. `messages` stays immediate
  // (low-frequency; the timeline dedup depends on it). Pure render
  // scheduling — the chatStore delta-merge logic is untouched.
  const currentAssistantMessage = useDeferredValue(_rtAssistantMessage);
  const currentThinking = useDeferredValue(_rtThinking);
  const currentSteps = useDeferredValue(_rtSteps);
  const currentToolCalls = useDeferredValue(_rtToolCalls);
  const currentEvents = useDeferredValue(_rtEvents);
  const { agentId, userId, agents, refreshAgents, checkAwarenessUpdate } = useConfigStore();
  // Creation studio: two touch points only — wrap the outgoing message, and
  // apply the settled reply's config block. Everything else lives in the hook.
  const { encodeOutgoing, applyFromReply } = useStudioTurn(agentId);
  const { t } = useTranslation();

  // Read artifact list at component scope so it can be safely passed into
  // ArtifactToolCallCards without calling a hook inside a .map() callback.
  const allArtifacts = useArtifactStore((s) => s.artifacts);

  useEffect(() => {
    if (agentId) setActiveAgent(agentId);
  }, [agentId, setActiveAgent]);

  // Per-agent draft persistence + restore-on-switch now live in <Composer>
  // (debounced, flushed on unmount; ChatPanel remounts it via key={agentId}).

  const currentAgent = useMemo(
    () => agents.find((a) => a.agent_id === agentId),
    [agents, agentId]
  );
  const isBootstrap = !!currentAgent?.bootstrap_active;
  // Only the owner may change an agent's model/framework (the llm-config
  // routes 403 anyone else) — same `created_by` gate as the profile page.
  const isOwner = !!currentAgent && !!userId && currentAgent.created_by === userId;
  // The backend persists its system greetings (generic and named) in English;
  // lib/bootstrapGreeting translates exactly those, before and after
  // persistence, and leaves scenario-authored greetings verbatim.
  const localizeBootstrapGreeting = (content?: string) => localizeBootstrapGreetingWith(content, t);
  const bootstrapGreeting = localizeBootstrapGreeting(currentAgent?.bootstrap_greeting);

  const [fastMode, setFastMode] = useFastMode(agentId);

  const { run, reconnect, stop, steer, isLoading } = useAgentWebSocket({
    onComplete: (completedAgentId: string) => {
      refreshAgents();
      if (completedAgentId) checkAwarenessUpdate(completedAgentId);
      onAgentComplete?.();
    },
  });

  // ── Phase C: auto-reconnect to an in-flight backend run ──────────
  //
  // When the user lands on this agent's chat panel and the backend
  // already has a BackgroundRun in 'running' state for them, the
  // panel should auto-reconnect (don't ask the user to "resend
  // their last message" — the agent is still working on it).
  //
  // We key the effect on agentId + run_id so:
  //   * switching agents drops the reconnect attempt for the previous
  //     agent (its WS, if any, was tied to a different agentId map key)
  //   * an agent transitioning into a NEW run later (different run_id)
  //     triggers a fresh reconnect
  //   * a run completing and the badge clearing won't infinitely
  //     re-open WS connections — the active_run goes null
  //
  // We deliberately don't attempt to reconnect when isStreaming for
  // this agent is already true: that means the local fresh-run flow
  // is already in flight on this tab.
  const activeRunId = currentAgent?.active_run?.run_id ?? null;
  useEffect(() => {
    if (!agentId || !userId) return;
    if (!activeRunId) return;
    if (isLoading) return; // already locally streaming → don't double-open
    // Fire-and-forget; wsManager handles idempotency + replacement.
    reconnect(agentId, userId, activeRunId, currentAgent?.name);
    // We intentionally exclude `reconnect` from the dep list — it's a
    // stable identity from useCallback in the hook.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId, userId, activeRunId]);

  // ── History loading ─────────────────────────────────
  const HISTORY_PAGE_SIZE = 20;

  // Conversation and Activity Log are two independently-paginated streams
  // (backend `include`): the inner tab IS the Activity Log, everything else is
  // the conversation. Fetching per-tab keeps a busy agent's activity flood from
  // eating the conversation tab's page budget, and keeps peer/team activity off
  // the "new reply" scroll/notify path on the conversation tab.
  const historyInclude = streamForTab(chatTab);

  // Active-stream views + writers. Downstream code (loadMore, poll, timeline,
  // loadMore-trigger) reads these unchanged; the writers target the active
  // stream's slot so a tab switch touches only that stream.
  const historyMessages = historyByStream[historyInclude];
  const historyLoaded = loadedByStream[historyInclude];
  const historyTotalCount = totalByStream[historyInclude];
  const setHistoryMessages = useCallback(
    (v: SimpleChatMessage[] | ((prev: SimpleChatMessage[]) => SimpleChatMessage[])) =>
      setHistoryByStream((p) => ({
        ...p,
        [historyInclude]:
          typeof v === 'function'
            ? (v as (prev: SimpleChatMessage[]) => SimpleChatMessage[])(p[historyInclude])
            : v,
      })),
    [historyInclude],
  );
  const setHistoryTotalCount = useCallback(
    (n: number) => setTotalByStream((p) => ({ ...p, [historyInclude]: n })),
    [historyInclude],
  );
  const setHistoryLoaded = useCallback(
    (b: boolean) => setLoadedByStream((p) => ({ ...p, [historyInclude]: b })),
    [historyInclude],
  );

  const loadChatHistory = useCallback(async () => {
    if (!agentId || !userId) return;
    setIsLoadingHistory(true);
    try {
      const response = await api.getSimpleChatHistory(
        agentId, HISTORY_PAGE_SIZE, 0, historyInclude,
      );
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
  }, [agentId, userId, historyInclude]);

  // Use ref for historyMessages length to avoid recreating loadMoreHistory on every poll
  const historyLengthRef = useRef(0);
  historyLengthRef.current = historyMessages.length;

  // Per-stream high-water timestamp for the "new messages arrived" poll check.
  // Keyed by include so switching tabs never compares against the other
  // stream's latest (which would suppress or spuriously fire the poll).
  const lastHistoryTimestampRef =
    useRef<Record<'chat' | 'activity', string>>({ chat: '', activity: '' });

  const loadMoreHistory = useCallback(async () => {
    if (!agentId || !userId || isLoadingMore) return;
    if (historyLengthRef.current >= historyTotalCount) return;

    setIsLoadingMore(true);
    shouldAutoScrollRef.current = false;
    const container = scrollContainerRef.current;

    try {
      const response = await api.getSimpleChatHistory(
        agentId, HISTORY_PAGE_SIZE, historyLengthRef.current, historyInclude,
      );
      if (response.success && response.messages.length > 0) {
        // Anchor on the topmost rendered item (see lib/scrollAnchor.ts for
        // why scrollHeight arithmetic alone lands the user on the new
        // chunk). Captured HERE — after the await, so the loading row has
        // already committed and its height is out of the correction — and
        // immediately before flushSync commits the prepended rows.
        const captured = container
          ? capturePrependAnchor(
              container,
              container.querySelector<HTMLElement>('[data-timeline-item]'),
            )
          : null;
        // flushSync so the DOM holds the prepended rows before measuring.
        flushSync(() => {
          setHistoryMessages((prev) => [...response.messages, ...prev]);
          setHistoryTotalCount(response.total_count);
        });

        if (container && captured) restorePrependAnchor(container, captured);
      }
    } catch (error) {
      console.error('Failed to load more chat history:', error);
    } finally {
      setIsLoadingMore(false);
    }
  }, [agentId, userId, historyTotalCount, isLoadingMore, historyInclude]);

  // One loader effect, two jobs, no double-fetch on mount (N3):
  //  • agent change / data wipe (the identity key moves) → reset BOTH streams
  //    and load the active one; the inactive stream loads lazily on tab switch.
  //  • same agent, tab switched → load the newly-active stream only if it has
  //    not loaded yet (already-loaded → instant, no clear / refetch / scroll
  //    reset — the friction the per-stream split removes).
  const historyRefreshTick = useChatStore((s) => s.historyRefreshTick);
  const historyIdentityRef = useRef('');
  useEffect(() => {
    if (!agentId || !userId) return;
    const identity = `${agentId}|${userId}|${historyRefreshTick}`;
    if (historyIdentityRef.current !== identity) {
      historyIdentityRef.current = identity;
      setHistoryByStream({ chat: [], activity: [] });
      setLoadedByStream({ chat: false, activity: false });
      setTotalByStream({ chat: 0, activity: 0 });
      lastHistoryTimestampRef.current = { chat: '', activity: '' };
      shouldAutoScrollRef.current = true;
      loadChatHistory();
      return;
    }
    if (!loadedByStream[historyInclude]) loadChatHistory();
    // loadChatHistory intentionally excluded: it re-identifies with
    // historyInclude and is captured fresh each render; listing it would just
    // re-run this effect without changing what it does.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId, userId, historyRefreshTick, historyInclude]);

  // ── Poll for new background messages ────────────────
  useEffect(() => {
    if (!agentId || !userId || !historyLoaded) return;

    if (historyMessages.length > 0) {
      const last = historyMessages[historyMessages.length - 1];
      if (last.timestamp && last.timestamp > lastHistoryTimestampRef.current[historyInclude]) {
        lastHistoryTimestampRef.current[historyInclude] = last.timestamp;
      }
    }

    const poll = async () => {
      if (document.hidden) return;
      try {
        const response = await api.getSimpleChatHistory(
          agentId, HISTORY_PAGE_SIZE, 0, historyInclude,
        );
        if (!response.success || response.messages.length === 0) return;

        const latestMsg = response.messages[response.messages.length - 1];
        const latestTs = latestMsg.timestamp || '';

        if (latestTs > lastHistoryTimestampRef.current[historyInclude]) {
          lastHistoryTimestampRef.current[historyInclude] = latestTs;
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

    // Poll once immediately so switching back to an already-loaded stream
    // refreshes right away instead of showing data up to 12s stale (N4). The
    // high-water + document.hidden guards inside `poll` make this a no-op when
    // nothing new arrived.
    void poll();
    const timer = setInterval(poll, 12_000);
    return () => clearInterval(timer);
  }, [agentId, userId, historyLoaded, historyInclude]);

  // ── Build unified timeline ──────────────────────────
  // History (DB) + session (chatStore) merged + de-duplicated. The dedup
  // (event_id-first, content heuristic fallback) lives in the pure,
  // unit-tested @/lib/buildTimeline so the logic — burned twice now,
  // Bug 19 and the "latest reply shown twice" bug — is testable in
  // isolation instead of buried in a useMemo.
  const timeline: TimelineItem[] = useMemo(
    () => buildUnifiedTimeline(historyMessages, messages),
    [historyMessages, messages],
  );

  // Per-tab visible items, computed BEFORE rendering so day separators can
  // compare each item against the previous VISIBLE one — comparing against
  // a neighbour that the tab filter hides would draw phantom separators.
  //
  // The run IN FLIGHT is also filtered out here. The backend persists a reply
  // row as soon as the reply tool executes, the 12s history poll picks it up
  // mid-run, and the streaming block below is already rendering that same
  // reply from currentEvents — so the answer drew twice, above and below the
  // pipeline phases, and a refresh "fixed" it only because the streaming copy
  // was gone by then. buildUnifiedTimeline's dedup cannot catch this: it
  // reconciles history against SESSION messages, and the in-flight turn has
  // no session message yet (stopStreaming is what writes it).
  //
  // Scoped three ways, and each one is load-bearing:
  //   - only while isStreaming, and only for currentRunId — the moment the run
  //     settles the row is ordinary history again and renders normally, so
  //     nothing is withheld from the reader (iron rule #16);
  //   - only role 'assistant'. The backend stamps the SAME event_id on the
  //     user's own row for that turn (chat_history.py builds both roles from
  //     one loop), and history rows win buildUnifiedTimeline's dedup, so an
  //     unscoped filter deletes the user's message from the screen while the
  //     agent works on it.
  const visibleTimeline: TimelineItem[] = useMemo(
    () =>
      timeline.filter((item) => {
        if (
          isStreaming && currentRunId
          && item.role === 'assistant' && item.eventId === currentRunId
        ) return false;
        const isInner = item.messageType === 'activity';
        return chatTab === 'inner' ? isInner : !isInner;
      }),
    [timeline, chatTab, isStreaming, currentRunId],
  );

  // The newest visible full message keeps its meta row (time) always
  // visible; older rows reveal it on hover (claude.ai convention).
  const lastMessageId = useMemo(() => {
    for (let i = visibleTimeline.length - 1; i >= 0; i--) {
      if (visibleTimeline[i].messageType !== 'activity') return visibleTimeline[i].id;
    }
    return null;
  }, [visibleTimeline]);

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

  // ── Creation studio: apply the config block when a turn settles ──
  //
  // Watches the streaming edge (true -> false) rather than the message list:
  // the draft block is only complete once the turn is done, and applying
  // mid-stream would write a half-serialised JSON value into the agent's
  // instructions. The reply text is read from the store's settled message,
  // not from the streaming buffer, for the same reason.
  //
  // Two guards, because `isStreaming` / `messages` are flat fields derived
  // from whichever agent is ACTIVE and this panel switches agents in place:
  //   - the edge is tracked per agent, so switching from a streaming agent A
  //     back to a settled agent B does not read as "B just settled";
  //   - each assistant message is applied at most once (by id), so a genuine
  //     re-settle on the same agent cannot replay an old draft over edits the
  //     user has since made in the panel. Content is not a usable key — the
  //     model is allowed to restate an identical block on a later turn.
  const wasStreamingByAgentRef = useRef<Record<string, boolean>>({});
  const appliedReplyIdsRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!agentId) return;
    const wasStreaming = wasStreamingByAgentRef.current[agentId] ?? false;
    wasStreamingByAgentRef.current[agentId] = isStreaming;
    if (!wasStreaming || isStreaming) return;
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const m = messages[i];
      if (m.role !== 'assistant') continue;
      const key = `${agentId}:${m.id}`;
      if (appliedReplyIdsRef.current.has(key)) return;
      appliedReplyIdsRef.current.add(key);
      void applyFromReply(m.content);
      return;
    }
  }, [agentId, isStreaming, messages, applyFromReply]);

  // ── Inner Thoughts defaults to the bottom (newest activity) ──
  // Like a chat log, not an inbox: the newest activity sits at the bottom, so
  // opening the tab (or a new activity arriving) snaps to the end and the user
  // sees the latest without scrolling down manually.
  const innerCount = useMemo(
    () => timeline.reduce((n, i) => (i.messageType === 'activity' ? n + 1 : n), 0),
    [timeline],
  );
  useEffect(() => {
    if (chatTab !== 'inner') return;
    const container = scrollContainerRef.current;
    if (!container) return;
    const id = requestAnimationFrame(() => {
      container.scrollTop = container.scrollHeight;
    });
    return () => cancelAnimationFrame(id);
  }, [chatTab, innerCount]);

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

  // ── Voice-input availability pre-flight ──────────────
  // We probe ONCE per (userId) — provider config doesn't change mid-session
  // for the chat view. If the user adds a provider in Settings during the
  // session, they'll see the click-time dialog one more time and then a
  // page reload picks up the new state. Cheaper than polling.
  useEffect(() => {
    if (!userId) return;
    let cancelled = false;
    api
      .getTranscriptionAvailability()
      .then((r) => {
        if (cancelled) return;
        setTranscriptionAvailable(r.available);
        setTranscriptionReason(r.reason);
      })
      .catch(() => {
        if (cancelled) return;
        // Probe failure → leave as undefined so click is allowed; the
        // post-upload banner will explain a real failure.
      });
    return () => {
      cancelled = true;
    };
  }, [userId]);

  // ── Attachment handlers ──────────────────────────────
  // `source='recording'` triggers backend Whisper transcription; the
  // default ('upload') means the user attached a file (Paperclip /
  // drag-drop / paste) and audio bytes are treated as opaque file
  // content, not dictation. Keeping the discriminator at the call
  // site rather than guessing from filename / MIME avoids fragile
  // heuristics and lets the AudioRecorder hand-pick its own path.
  const uploadAttachments = useCallback(
    async (files: File[], opts?: { source?: 'recording' | 'upload' }) => {
      if (!agentId || !userId || files.length === 0) return;
      // A mid-run steer is text-only (v1): an attachment can't be folded into a
      // live turn. The attach + mic buttons are already disabled while
      // streaming, but drag-drop and paste reach here directly — so gate the
      // single funnel every upload path flows through. We block BEFORE the
      // network upload, so nothing is orphaned on the backend and no dead chip
      // appears that could never be sent. Surface a dismissible notice instead
      // of silently swallowing the drop.
      if (isLoading) {
        setTranscriptionNotice(t('chat.composer.attachDuringRun'));
        return;
      }
      setUploadingCount((n) => n + files.length);
      for (const file of files) {
        try {
          const resp = await api.uploadAttachment(agentId, file, opts);
          if (resp.success && resp.file_id && resp.mime_type && resp.category) {
            setPendingAttachments((prev) => [
              ...prev,
              {
                file_id: resp.file_id!,
                mime_type: resp.mime_type!,
                original_name: resp.original_name ?? file.name,
                size_bytes: resp.size_bytes ?? file.size,
                category: resp.category!,
                // Render-path discriminator (recording vs upload).
                // Echoed by backend so the persisted attachment dict
                // carries it through chat history reload. Falls back
                // to the local request hint for forward compat.
                source: (resp.source ?? opts?.source ?? 'upload') as
                  | 'recording'
                  | 'upload',
                // Whisper transcript — populated for any audio/* upload
                // the user can transcribe (the backend runs Whisper for
                // both source values). Forwarded into the WS payload so
                // the agent's attachment marker carries the spoken
                // content for both voice memos AND uploaded files.
                transcript: resp.transcript ?? undefined,
              },
            ]);
            // Backstop for the race: availability probe said true at
            // mount but the actual upload found nothing (provider was
            // deleted between the two calls). Mirror the dialog with
            // a banner so the user still sees something concrete.
            const isRecording = (resp.source ?? opts?.source) === 'recording';
            if (isRecording && resp.transcription_available === false) {
              // Sync the cached availability state and surface the
              // dialog the next time they tap mic.
              setTranscriptionAvailable(false);
              setTranscriptionNotice(t('chat.audio.noLongerAvailable'));
            } else if (isRecording && resp.transcript) {
              // New successful recording → clear any stale notice
              setTranscriptionNotice(null);
            }
          } else {
            console.error('Attachment upload failed:', resp.error);
          }
        } catch (e) {
          console.error('Attachment upload error:', e);
          // 402 means auth_middleware's provider_resolver gated the
          // request — user has no LLM provider and no (budgeted) free
          // tier. STT can't proceed because the whole agent path is
          // gated. Surface the same dialog as a normal "no transcription
          // provider" state — the call to action is identical
          // (configure a provider).
          //
          // This branch is the safety net; a healthy click should
          // already have been blocked by `onPreflight` re-probing
          // /api/transcription/availability before MediaRecorder starts.
          // Keeping this branch costs us nothing and protects against
          // races (quota exhausted between preflight and upload).
          const msg = String((e as Error)?.message ?? e);
          if (msg.includes('402') && (opts?.source === 'recording')) {
            setTranscriptionAvailable(false);
            setTranscriptionReason('free_tier_not_granted');
            setVoiceUnavailableDialogOpen(true);
          }
        } finally {
          setUploadingCount((n) => Math.max(0, n - 1));
        }
      }
    },
    [agentId, userId, t, isLoading],
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
    // No composer on the Activity Log tab → nothing to attach to. Bail before
    // preventDefault so the browser keeps its own "not a drop target" cursor
    // instead of us claiming the drag and then dropping it on the floor.
    if (!agentId || isActivityTab) return;
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
    if (!agentId || isActivityTab) return;
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
  // One submit at a time. `handleSubmit` awaits (the studio's encode, which
  // reads the agent's instructions) BEFORE the composer clears and
  // `startStreaming` flips `isLoading`, so a second Enter in that window used
  // to run the whole path again and send the same message twice.
  const submittingRef = useRef(false);
  const handleSubmit = async () => {
    if (submittingRef.current) return;
    submittingRef.current = true;
    try {
      await submitOnce();
    } finally {
      submittingRef.current = false;
    }
  };

  const submitOnce = async () => {
    const trimmed = (composerRef.current?.getText() ?? '').trim();
    const hasContent = trimmed.length > 0 || pendingAttachments.length > 0;
    if (!hasContent || !agentId || !userId || uploadingCount > 0) return;

    const content = trimmed;

    // Mid-run follow-up: a run is streaming. The composer stays editable during a
    // run (draft-while-running), so we DO reach here — but only a steerable run
    // sends: fold the message into the SAME turn. A non-steerable run returns
    // silently, leaving the draft to be sent once the run ends (the pre-steer
    // behavior). Attachments are not supported mid-run in v1 — a steer is text only.
    if (isLoading) {
      if (!currentSteerable || trimmed.length === 0) return;
      // generateId, not crypto.randomUUID (undefined on non-secure http://<ip>
      // contexts — the repo's productAnalytics/utils already guard this).
      const clientMsgId = generateId();
      const sent = steer(agentId, content, clientMsgId);
      // Always add the bubble so the user gets feedback either way. On send it's
      // 'queued' (the backend acks it merged/rejected). If the socket was no
      // longer steerable (sent===false: CONNECTING/CLOSING/closed) mark it
      // rejected right away rather than dropping silently. Attachments are NOT
      // cleared — a steer is text-only (v1), so they stay for the next message.
      addSteerMessage(agentId, content, clientMsgId);
      if (sent) {
        composerRef.current?.clear();
        shouldAutoScrollRef.current = true;
        initialScrollPendingRef.current = true;
      } else {
        markSteerRejected(agentId, clientMsgId, 'not_sent');
      }
      return;
    }

    // Creation studio: while it is open, every outgoing message carries the
    // Agent Builder instruction plus this agent's CURRENT configuration.
    // Restating the config each turn is what makes the user's own panel edits
    // authoritative — the model revises what the panel holds instead of
    // overwriting it. Returns `content` untouched when the studio is shut, or
    // on any failure: a studio hiccup must not swallow the user's message.
    // Deliberately after the steer branch — a mid-run steer is not a turn.
    const outgoing = await encodeOutgoing(content);

    const attachmentsToSend = pendingAttachments;
    composerRef.current?.clear();
    setPendingAttachments([]);
    shouldAutoScrollRef.current = true;
    // Bug 15: snap to bottom for the user's freshly-sent bubble before
    // streaming starts. The streaming effect takes over from there.
    initialScrollPendingRef.current = true;

    if (bootstrapGreetingPending) {
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
                content: bootstrapGreeting,
                timestamp: Date.now() - 1,
              },
              ...(state.agentSessions[agentId]?.messages ?? []),
            ],
          },
        },
      }));
    }

    addUserMessage(agentId, outgoing, attachmentsToSend.length ? attachmentsToSend : undefined);
    startStreaming(agentId);
    captureProductEvent('message_submitted', {
      agent_id: agentId,
      trigger_source: 'chat',
    });

    try {
      const agentName = currentAgent?.name || agentId;
      run(agentId, userId, outgoing, agentName, attachmentsToSend.length ? attachmentsToSend : undefined, fastMode);
    } catch (error) {
      console.error('Failed to run agent:', error);
    }
  };

  // handleKeyDown (Enter→send) + IME composition handling now live in
  // <Composer>; it calls back into handleSubmit on Enter.

  // Stable wrappers so the memoized <Composer> doesn't re-render on every
  // ChatPanel render (e.g. streaming deltas) — they always call the latest
  // closure via a ref, so no stale values and no dependency lists.
  const submitFnRef = useRef(handleSubmit);
  submitFnRef.current = handleSubmit;
  const dragFnsRef = useRef({ over: handleDragOver, leave: handleDragLeave, drop: handleDrop, paste: handlePaste });
  dragFnsRef.current = { over: handleDragOver, leave: handleDragLeave, drop: handleDrop, paste: handlePaste };
  const stableSubmit = useCallback(() => submitFnRef.current(), []);
  const stableDragOver = useCallback((e: React.DragEvent<HTMLElement>) => dragFnsRef.current.over(e), []);
  const stableDragLeave = useCallback((e: React.DragEvent<HTMLElement>) => dragFnsRef.current.leave(e), []);
  const stableDrop = useCallback((e: React.DragEvent<HTMLElement>) => dragFnsRef.current.drop(e), []);
  const stablePaste = useCallback((e: React.ClipboardEvent<HTMLTextAreaElement>) => dragFnsRef.current.paste(e), []);

  // Zero-state is PER TAB, and it keys on `visibleTimeline` — exactly the rows
  // this tab renders — not on the raw stream + session pair. Two bugs came out
  // of the old form: the conversation's onboarding card / bootstrap greeting
  // were drawn on an empty Activity Log too (so switching tabs looked like a
  // dead button — the user could never reach the legitimately blank Activity
  // Log), and any live session message (conversation-only) suppressed the
  // Activity Log's zero-state into an unexplained blank area.
  // (`isActivityTab` is declared with the tab state above — the attachment
  // intake handlers need it before this point.)
  // Day-zero of the CONVERSATION. Deliberately tab-independent (it reads the
  // chat stream's own slot, not the active one) because handleSubmit also uses
  // it to fold the greeting into the session on the user's first send — which
  // can happen while the Activity Log tab is open.
  const bootstrapGreetingPending =
    isBootstrap && loadedByStream.chat && historyByStream.chat.length === 0 && messages.length === 0;
  const tabIsEmpty = historyLoaded && visibleTimeline.length === 0;
  const showBootstrapGreeting = !isActivityTab && bootstrapGreetingPending;
  const showEmptyState = !isActivityTab && !showBootstrapGreeting && tabIsEmpty && !isStreaming;
  const showActivityEmptyState = isActivityTab && tabIsEmpty;

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
      {/* v4 header — agent-name protagonist (now a link into the agent's
          profile page); Chat / Inner Thoughts segmented toggle; Jobs / Inbox /
          Artifacts / Cost entries and the ⋯ detail menu (doors only — panels
          unchanged). Hidden on mobile: the top strip shows the breadcrumb
          there and the tab pair below stands in. */}
      <ChatHeader
        agentId={agentId}
        agentName={currentAgent?.name || agentId || 'AI'}
        isStreaming={isStreaming}
        currentSteps={currentSteps}
        chatTab={chatTab}
        onChatTabChange={setChatTab}
        onOpenAgentConfig={isOwner ? () => setAgentCfgOpen(true) : undefined}
      />

      {/* Mobile-only Chat / Inner Thoughts tabs — the desktop toggle lives
          in the header, which doesn't render on < md. */}
      <div
        className="px-3 flex md:hidden items-center gap-1 border-b"
        style={{ borderColor: 'var(--nm-hairline)' }}
      >
        {(['conversation', 'inner'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setChatTab(tab)}
            className={cn(
              'px-3 py-2 text-[11px] font-mono uppercase tracking-[0.12em] transition-colors border-b-2 -mb-px',
              chatTab === tab
                ? 'border-[var(--accent-primary)] text-[var(--text-primary)]'
                : 'border-transparent text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]',
            )}
          >
            {tab === 'conversation' ? t('chat.conversation') : t('chat.innerThoughts')}
          </button>
        ))}
      </div>

      {/* Messages area — single unified timeline.
          Wrapped in <ScrollArea> so the scrollbar is JS-rendered (Radix) and
          cannot be hijacked by macOS's "always show scrollbars" AppKit
          fallback that ignores ::-webkit-scrollbar. The viewport ref is
          forwarded so existing scroll logic (auto-scroll-to-bottom, history
          load on scroll-top, anchor preservation) reads/writes the SAME
          element it always did. */}
      <ScrollArea
        className="flex-1 min-h-0"
        data-help-id="chat.messages"
        viewportRef={scrollContainerRef}
        viewportClassName="py-6 px-6 max-md:p-3"
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
      {/* v4: the conversation reads in a centered 820px measure regardless
          of how wide the (now full-bleed) chat column gets. */}
      <div className="mx-auto w-full max-w-[820px] space-y-4">
        {/* Loading more (top) — NM bracket-loading placeholder */}
        {isLoadingMore && (
          <div className="flex items-center justify-center py-2">
            <BracketLoading label="Loading older messages" />
          </div>
        )}

        {/* Initial loading */}
        {isLoadingHistory && (
          <div className="flex items-center justify-center py-4">
            <BracketLoading label="Loading chat history" />
          </div>
        )}

        {/* Empty state. With an agent selected → the v4 in-stream onboarding
            card ("<Agent> is ready" + suggested prompts that fill the
            composer; dismissible, persisted per agent). With no agent → the
            plain bracket prompt to pick one from the sidebar. */}
        {showEmptyState && (
          agentId ? (
            <OnboardingJourney
              // dismissed is read from localStorage once per mount; without
              // the key, switching agents reuses the instance and agent A's
              // dismissal leaks onto agent B (and vice versa).
              key={agentId}
              agentId={agentId}
              onPrompt={(text) => composerRef.current?.setText(text)}
            />
          ) : (
            <BracketEmptyState
              label={t('chat.selectAgent')}
              hint={t('chat.selectAgentHint')}
            />
          )
        )}

        {/* Activity Log zero-state. An agent that has never run on its own has
            a genuinely empty stream — say so, instead of handing the tab the
            conversation's onboarding card (which read as "the tab button does
            nothing") or an unexplained blank column. */}
        {showActivityEmptyState && (
          <BracketEmptyState
            label={agentId ? t('chat.activityEmpty') : t('chat.selectAgent')}
            hint={agentId ? t('chat.activityEmptyHint') : t('chat.selectAgentHint')}
          />
        )}

        {/* Bootstrap greeting */}
        {showBootstrapGreeting && (
          <div className="animate-slide-up">
            <MessageBubble
              message={{
                id: 'bootstrap-greeting',
                role: 'assistant',
                content: bootstrapGreeting,
                timestamp: Date.now(),
              }}
              agentId={agentId}
              isLatest
            />
          </div>
        )}

        {/* Unified timeline. Tab routing (what belongs in Inner Thoughts vs
            Conversation) lives in the visibleTimeline memo above: activity
            records — the lightweight "a background turn happened" markers —
            are the ONLY thing routed to Inner Thoughts; everything the agent
            actually *says* is owner-facing and stays in Conversation,
            including its cross-channel "I replied / notified you" narrations
            (the owner-facing tool targets the owner regardless of
            which channel triggered the turn). Session items (the owner's
            live in-app turn) are conversation too.

            Messages only show HH:mm:ss, so a history spanning days had no
            date context — insert a separator at every local-day boundary
            (Today / Yesterday / date, matching IM convention). */}
        {visibleTimeline.map((item, idx) => {
          const dayInfo = chatDayInfo(item.timestamp);
          const prevKey = idx > 0 ? chatDayInfo(visibleTimeline[idx - 1].timestamp).key : null;
          const separator = dayInfo.key !== prevKey ? (
            <div
              data-testid="chat-day-separator"
              className="flex items-center gap-3 px-6 py-1.5"
            >
              <span className="flex-1 h-px bg-[var(--border-subtle)]" />
              <span className="text-[10px] font-mono text-[var(--text-tertiary)]">
                {dayInfo.kind === 'today'
                  ? t('chat.dateToday')
                  : dayInfo.kind === 'yesterday'
                    ? t('chat.dateYesterday')
                    : dayInfo.label}
              </span>
              <span className="flex-1 h-px bg-[var(--border-subtle)]" />
            </div>
          ) : null;

          // Activity record → source-labelled, expandable card (Inner Thoughts only).
          if (item.messageType === 'activity') {
            return (
              <div key={item.id} data-timeline-item>
                {separator}
                <InnerThoughtCard item={item} agentId={agentId} />
              </div>
            );
          }

          // Full message bubble (Conversation: owner↔agent chat; Inner Thoughts:
          // the agent's cross-channel narrations, readable, with their own
          // inline reasoning/tool disclosure).
          const isNewSession = item.source === 'session';
          const hasArtifactTools =
            item.role === 'assistant' &&
            !!agentId &&
            !!item.toolCalls?.some((tc) => isArtifactToolName(tc.tool_name) && tc.tool_output);
          return (
            <div
              key={item.id}
              data-timeline-item
              // Turn rhythm. With the agent's turn unframed, spacing is what
              // separates one exchange from the next: extra air opens each
              // user message, so a turn reads as [air] question [close]
              // document [air] question. A hairline was the alternative and
              // was rejected — one rule per turn stacks into a ledger down a
              // long conversation, and the right-aligned user bubble is
              // already a strong enough anchor to start from.
              className={cn(
                item.role === 'user' && 'mt-6',
                isNewSession && 'animate-slide-up',
              )}
            >
              {separator}
              <MessageBubble
                message={{
                  id: item.id,
                  role: item.role,
                  content:
                    item.role === 'assistant'
                      ? localizeBootstrapGreeting(item.content)
                      : item.content,
                  timestamp: item.timestamp,
                  thinking: item.thinking,
                  toolCalls: item.toolCalls,
                  attachments: item.attachments,
                  timeline: item.timeline,
                  isError: item.isError,
                  warnings: item.warnings,
                  actionReason: item.actionReason,
                }}
                eventId={item.eventId}
                agentId={agentId}
                isLatest={item.id === lastMessageId}
              />
              {/* Render inline artifact preview cards for register_artifact
                  tool calls that returned an artifact_id */}
              {hasArtifactTools && agentId && item.toolCalls && (
                <ArtifactToolCallCards
                  toolCalls={item.toolCalls}
                  allArtifacts={allArtifacts}
                />
              )}
            </div>
          );
        })}

        {/* The in-flight turn, rendered exactly as it will settle: narration,
            tool lines and collapsed reasoning in the order they arrive, with
            the reply growing underneath. No avatar and no bubble, matching
            the settled agent turn in MessageBubble — the whole point is that
            nothing rearranges when the run lands. */}
        {chatTab === 'conversation' && isStreaming && (
          <div className="animate-fade-in">
            {/* Reconnected-to-ongoing-run badge (Shenzhen-r2 B1): the
                replay below is the SAME run continuing after a refresh /
                reconnect — label it, with elapsed anchored to the run's
                real start, so it cannot be read as a fresh generation.
                runId must match the streaming turn (review #349 M4): the
                anchor may only badge ITS run — if a second start-stream
                path ever appears, a stale anchor stays invisible instead
                of silently badging someone else's turn. */}
            {resumedRun && resumedRun.runId === currentRunId && (
              <ResumedRunChip startedAtMs={resumedRun.startedAtMs} />
            )}
            {/* The run's preamble: the backend pipeline phases that happen
                before the model produces anything. Without it the window
                between "send" and the first narration is blank. It heads
                the document rather than sitting in a box beside it. */}
            <RunPhases
              events={currentEvents}
              steps={currentSteps}
              startedAtMs={
                resumedRun && resumedRun.runId === currentRunId
                  ? resumedRun.startedAtMs
                  : undefined
              }
            />
            {/* The live turn IS the document — narration, tool lines and
                collapsed reasoning in the order they happen, the reply
                growing underneath. Same component, same shape the settled
                turn keeps, so nothing rearranges when it lands.

                showProcess is on here (it used to be off, with the process
                living in a framed panel above the composer): that panel is
                gone, so there is nothing to paint twice, and the narration
                is finally IN the reading flow rather than in a side box
                between reasoning rows. */}
            <SegmentedReply
              segments={segmentTurn(currentEvents)}
              showProcess
              isStreaming
            />
            {/* Mid-stream artifact preview is independent of the timeline:
                it surfaces created/uploaded artifacts inline as soon as
                their tool_output lands, without waiting for the whole
                turn to finish. */}
            {agentId && currentToolCalls.length > 0 && (
              <ArtifactToolCallCards
                toolCalls={currentToolCalls}
                allArtifacts={allArtifacts}
              />
            )}
          </div>
        )}

        {/* The old "starting up… / loading context…" indicator that
            floated here is now RunPhases, at the head of the in-flight
            document — the phases read as the run's preamble, in the same
            column as everything else the agent does. */}

        {/* Scroll anchor. max-md:-mt-4 cancels the space-y-4 margin this empty
            div would otherwise add, killing the dead gap below the last message
            on mobile (combined with the smaller viewport padding below). */}
        <div ref={messagesEndRef} className="max-md:-mt-4" />
      </div>
      </ScrollArea>

      {/* Input area — CONVERSATION ONLY. The Activity Log is a read-only window
          onto runs the agent started by itself (jobs, channels, teammates);
          there is no reply channel to type into, so the whole footer goes —
          composer, attach/voice row, transcription notice and the live
          ProcessPanel alike, since every one of them describes the owner↔agent
          run. This matches the live reply block above, already gated to
          `chatTab === 'conversation'`. Unmounting <Composer> does NOT lose a
          draft: it flushes to the per-agent draft store on unmount and restores
          on remount (see Composer.tsx).
          Drop is handled at the Card root, so this wrapper no longer needs its
          own onDragOver/onDragLeave/onDrop. Inner wrapper mirrors the 820px
          stream measure (v4). */}
      {!isActivityTab && (
      <div className="border-t border-[var(--rule)]">
      <div className="mx-auto w-full max-w-[820px] px-6 max-md:px-3 pt-3.5 pb-3">
        {/* Audio transcription unavailable notice — only shown when an
            audio upload returned transcription_available=false. */}
        {transcriptionNotice && (
          <div className="mb-2.5 flex items-start gap-2 rounded-[var(--radius-md)] border border-[var(--rule)] bg-[var(--bg-tertiary)]/60 px-3 py-2 text-xs text-[var(--text-secondary)]">
            <span className="flex-1">{transcriptionNotice}</span>
            <button
              type="button"
              onClick={() => setTranscriptionNotice(null)}
              className="p-0.5 rounded hover:bg-[var(--nm-paper-warm)]"
              title="Dismiss"
            >
              <X className="w-3 h-3 text-[var(--text-tertiary)]" />
            </button>
          </div>
        )}
        {/* Pending attachments preview row */}
        {(pendingAttachments.length > 0 || uploadingCount > 0) && (
          <div className="mb-2.5 flex flex-wrap gap-2">
            {pendingAttachments.map((att) => {
              const haveCreds = !!agentId && !!userId;
              const isImage = att.category === 'image';
              const isVoiceMemo = att.source === 'recording';
              const canPreviewImage = isImage && haveCreds;
              return (
                <div
                  key={att.file_id}
                  className="relative flex items-center gap-2 rounded-[var(--radius-md)] border border-[var(--rule)] bg-[var(--bg-tertiary)]/60 pr-7 pl-1.5 py-1 max-w-[300px]"
                >
                  {isVoiceMemo ? (
                    <VoiceTranscript compact transcript={att.transcript} />
                  ) : (
                    <>
                      {canPreviewImage ? (
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
                    </>
                  )}
                  <button
                    type="button"
                    onClick={() => handleRemoveAttachment(att.file_id)}
                    className="absolute right-1 top-1 p-0.5 rounded hover:bg-[var(--nm-paper-warm)]"
                    title="Remove"
                  >
                    <X className="w-3 h-3 text-[var(--text-tertiary)]" />
                  </button>
                </div>
              );
            })}
            {uploadingCount > 0 && (
              <div className="flex items-center gap-1.5 px-2 py-1 rounded-[var(--radius-md)] border border-dashed border-[var(--rule)] text-[10px] text-[var(--text-tertiary)] font-[family-name:var(--font-mono)] uppercase tracking-[0.1em]">
                <Loader2 className="w-3 h-3 animate-spin" />
                Uploading {uploadingCount}
              </div>
            )}
          </div>
        )}

        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={handleFilePick}
        />
        {/* clawcreek-style composer: the textarea owns the box, the send/stop
            button is docked bottom-right inside it, and a tools row (attach +
            voice) sits beneath. Textarea state is isolated in <Composer> so a
            keystroke re-renders only it, not this monolith (see Composer.tsx);
            key={agentId} remounts it on agent switch to restore that agent's
            draft. The drag/paste handlers live on the textarea too because the
            native default (insert dropped path / paste-as-text) wins otherwise. */}
        {/* The plan is the one piece of the run that must not scroll away —
            it answers "where are we now" — so it stays pinned here while the
            rest of the process reads inline in the document above. Mounted
            only while streaming; the settled turn carries its own process. */}
        {isStreaming && <PlanStrip events={currentEvents} />}

        <div className="relative" data-help-id="chat.composer">
          <Composer
            key={agentId ?? '__none__'}
            ref={composerRef}
            agentId={agentId}
            // Editable whenever an agent is selected — including during a run
            // (draft-while-running is a pre-existing capability; Composer.tsx's
            // contract is "disabled === no agent"). SENDING is what's gated: a
            // steerable run folds the text in, a non-steerable run holds the
            // draft until it ends (see handleSubmit). Only the placeholder
            // changes to invite a mid-run follow-up when steerable.
            disabled={!agentId}
            placeholder={
              !agentId
                ? t('chat.composer.selectAgentFirst')
                : isDragging
                  ? t('chat.composer.dropFile')
                  : isLoading && currentSteerable
                    ? t('chat.composer.steerPlaceholder')
                    : t('chat.composer.placeholder')
            }
            onSubmit={stableSubmit}
            onEmptyChange={setComposerEmpty}
            onDragOver={stableDragOver}
            onDragLeave={stableDragLeave}
            onDrop={stableDrop}
            onPaste={stablePaste}
            // A steerable run overlays TWO buttons (Stop + steer Send); every
            // other state overlays one. Tell the input how much right padding
            // to reserve so typed text never slides under the steer button.
            trailingSlots={isStreaming && currentSteerable ? 2 : 1}
          />
          {/* Send / Stop — vertically centered at the right edge of the
              textarea. The send (↵) button rests neutral gray and lights up in
              the Carbon (human) palette — carbon-soft fill + carbon border +
              carbon glyph, matching the user's own chat bubble — only once the
              composer has content (text or an attachment), not merely on
              focus/hover. Stop stays oxblood (danger). */}
          {isStreaming ? (
            <>
              {/* On a STEERABLE run, a mid-run send button sits left of Stop so
                  mouse/touch users have a real submit target (not Enter-only —
                  mobile keyboards often make Enter a newline). Its disabled does
                  NOT include isLoading (we ARE streaming); handleSubmit folds the
                  text into the current turn. Non-steerable runs show only Stop. */}
              {currentSteerable && (
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={handleSubmit}
                  // A steer is TEXT-only, and handleSubmit returns on empty text,
                  // so gate purely on `composerEmpty` — NO attachment escape here
                  // (an attachment-only steer would light the button but do
                  // nothing). The fresh-run button below keeps its attachment
                  // escape; the two are distinct.
                  disabled={composerEmpty || !agentId || uploadingCount > 0}
                  className={cn(
                    'absolute right-12 top-1/2 -translate-y-1/2 h-9 w-9 rounded-[var(--radius-lg)] border transition-colors',
                    !composerEmpty
                      ? 'border-[var(--color-carbon)] bg-[var(--color-carbon-soft)] text-[var(--color-carbon)] hover:bg-[var(--color-carbon-soft)] hover:text-[var(--color-carbon)]'
                      : 'border-[var(--nm-hairline)] bg-[var(--nm-paper-warm)] text-[var(--text-tertiary)]',
                  )}
                  title={t('chat.steer.sendTitle')}
                >
                  <CornerDownLeft className="w-4 h-4" />
                </Button>
              )}
              <Button
                variant="danger"
                size="icon"
                onClick={() => agentId && stop(agentId)}
                className="absolute right-2 top-1/2 -translate-y-1/2 h-9 w-9"
                title="Stop generation"
              >
                <Square className="w-4 h-4 fill-current" />
              </Button>
            </>
          ) : (
            <Button
              variant="ghost"
              size="icon"
              onClick={handleSubmit}
              disabled={
                (composerEmpty && pendingAttachments.length === 0)
                || isLoading
                || !agentId
                || uploadingCount > 0
              }
              className={cn(
                'absolute right-2 top-1/2 -translate-y-1/2 h-9 w-9 rounded-[var(--radius-lg)] border transition-colors',
                !composerEmpty || pendingAttachments.length > 0
                  ? 'border-[var(--color-carbon)] bg-[var(--color-carbon-soft)] text-[var(--color-carbon)] hover:bg-[var(--color-carbon-soft)] hover:text-[var(--color-carbon)]'
                  : 'border-[var(--nm-hairline)] bg-[var(--nm-paper-warm)] text-[var(--text-tertiary)]',
              )}
              title="Send (Enter)"
            >
              <CornerDownLeft className="w-4 h-4" />
            </Button>
          )}
        </div>

        {/* Tools row — attach (+) and voice on the left, followed by the
            always-visible privacy reminder (v4: the old banner folded into
            this row — permanent, non-dismissible, full text via the title
            tooltip); fast-mode toggle (ComposerFastToggle) and model badge
            (ComposerModelBadge) on the right. */}
        <div className="mt-1 flex items-center justify-between gap-2">
          <div className="flex items-center gap-0.5 min-w-0">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => fileInputRef.current?.click()}
            disabled={!agentId || isLoading}
            className="h-8 w-8 text-[var(--text-secondary)] hover:bg-transparent hover:text-[var(--color-carbon)]"
            title="Attach file"
          >
            <Plus className="w-4 h-4" />
          </Button>
          {/* Voice capture: hands the recorded blob to the same upload path as
              the attach button / drag-drop, so transcription, the
              "transcription unavailable" notice, and the pendingAttachments
              chip all behave identically across input methods. */}
          <AudioRecorder
            disabled={!agentId || isLoading}
            onRecorded={(file) => uploadAttachments([file], { source: 'recording' })}
            onError={(msg) => setTranscriptionNotice(msg)}
            available={transcriptionAvailable}
            onUnavailable={() => setVoiceUnavailableDialogOpen(true)}
            onPreflight={async () => {
              // Click-time refresh of the availability cache. Without
              // this, toggling "Use free quota" in Settings doesn't
              // invalidate the value the AudioRecorder sees from the
              // mount-time useEffect.
              if (!userId) return false;
              try {
                const r = await api.getTranscriptionAvailability();
                setTranscriptionAvailable(r.available);
                setTranscriptionReason(r.reason);
                if (!r.available) {
                  setVoiceUnavailableDialogOpen(true);
                  return false;
                }
                return true;
              } catch {
                // Network blip — don't block recording. Upload-time
                // error handler will surface real failures.
                return true;
              }
            }}
          />
          {/* Security notice (2026-06-17 hardening): demoted from the banner
              to the tools row, but it must stay readable — no truncation, and
              at least the mid ink step. The title carries the long-form text
              (and zh-localization.test.ts asserts on it). */}
          <span
            className="ml-2 min-w-0 line-clamp-2 text-[11px] leading-tight text-[var(--nm-ink50)]"
            title={t('chat.securityReminder')}
          >
            {t('chat.composerPrivacyHint')}
          </span>
          </div>
          <div className="flex items-center gap-1.5">
            <ComposerFastToggle
              enabled={fastMode}
              onToggle={setFastMode}
              disabled={!agentId}
            />
            <ComposerModelBadge agentId={agentId} reloadKey={modelReloadKey} />
          </div>
        </div>
      </div>
      </div>
      )}

      {/* Per-agent model & framework panel (opened from the header). */}
      {agentId && isOwner && (
        <AgentLlmConfigPanel
          agentId={agentId}
          isOpen={agentCfgOpen}
          onClose={() => setAgentCfgOpen(false)}
          onSaved={() => {
            setModelReloadKey((k) => k + 1);
            void refreshAgents();
          }}
        />
      )}

      {/* Voice-input unavailable dialog. Triggered by clicking the mic
          button when the availability probe came back false — we surface
          the missing-provider state up-front rather than letting the
          user record audio that can't be transcribed. */}
      <Dialog
        isOpen={voiceUnavailableDialogOpen}
        onClose={() => setVoiceUnavailableDialogOpen(false)}
        title={t('chat.audio.unavailableTitle')}
        size="md"
      >
        <DialogContent>
          <div className="flex items-start gap-3">
            <div className="w-9 h-9 rounded-full bg-[var(--bg-tertiary)] flex items-center justify-center shrink-0">
              <Mic className="w-4 h-4 text-[var(--text-secondary)]" />
            </div>
            <div className="flex-1 text-sm leading-relaxed text-[var(--text-secondary)]">
              {transcriptionReason === 'free_tier_not_granted' ? (
                <p>{t('chat.audio.unavailableFreeTier')}</p>
              ) : transcriptionReason === 'none_openai_only' ? (
                <>
                  <p>{t('chat.audio.unavailableOpenaiOnlyIntro')}</p>
                  <ul className="mt-2 ml-4 list-disc space-y-1 text-[var(--text-tertiary)]">
                    <li>{t('chat.audio.unavailableOpenaiOnlyItem1')}</li>
                    <li>{t('chat.audio.unavailableOpenaiOnlyItem2')}</li>
                    <li>{t('chat.audio.unavailableOpenaiOnlyItem3')}</li>
                  </ul>
                  <p className="mt-3">{t('chat.audio.unavailableOpenaiOnlyOutro')}</p>
                </>
              ) : (
                <>
                  <p>{t('chat.audio.unavailableDefaultIntro')}</p>
                  <ul className="mt-2 ml-4 list-disc space-y-1 text-[var(--text-tertiary)]">
                    <li>{t('chat.audio.unavailableDefaultItem1')}</li>
                    <li>{t('chat.audio.unavailableDefaultItem2')}</li>
                  </ul>
                  <p className="mt-3">{t('chat.audio.unavailableDefaultOutro')}</p>
                </>
              )}
              {transcriptionReason === 'unknown' && (
                <p className="mt-2 text-xs text-[var(--text-tertiary)] italic">
                  {t('chat.audio.unavailableProbeFailed')}
                </p>
              )}
            </div>
          </div>
        </DialogContent>
        <DialogFooter>
          <Button
            variant="ghost"
            onClick={() => setVoiceUnavailableDialogOpen(false)}
          >
            {t('chat.audio.cancel')}
          </Button>
          <Button
            variant="accent"
            onClick={() => {
              setVoiceUnavailableDialogOpen(false);
              navigate('/app/settings');
            }}
          >
            {t('chat.audio.openSettings')}
          </Button>
        </DialogFooter>
      </Dialog>
    </Card>
  );
}
