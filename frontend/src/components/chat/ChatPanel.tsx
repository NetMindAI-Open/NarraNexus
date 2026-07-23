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
import { CornerDownLeft, Square, Loader2, Plus, X, FileText, Image as ImageIcon, Mic, SlidersHorizontal } from 'lucide-react';
import { flushSync } from 'react-dom';
import { Card, Button, ScrollArea } from '@/components/ui';
import { CostPopover } from '@/components/cost/CostPopover';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/Dialog';
import { BracketEmptyState, BracketLoading, BracketSectionLabel, BindingDot, RingAvatar } from '@/components/nm';
import { OnboardingJourney } from './OnboardingJourney';
import { ComposerModelBadge } from './ComposerModelBadge';
import { AgentLlmConfigPanel } from './AgentLlmConfigPanel';
import { useChatStore, useConfigStore, useArtifactStore } from '@/stores';
import { useAgentWebSocket } from '@/hooks';
import { cn } from '@/lib/utils';
import { api } from '@/lib/api';
import { buildUnifiedTimeline, type TimelineItem } from '@/lib/buildTimeline';
import { getChatDraft } from '@/lib/chatDrafts';
import { artifactsApi } from '@/services/artifactsApi';
import { MessageBubble } from './MessageBubble';
import { InnerThoughtCard } from './InnerThoughtCard';
import { TurnTimeline } from './TurnTimeline';
import { ExecutionPopover } from './ExecutionPopover';
import { Composer, type ComposerHandle } from './Composer';
import { AttachmentImage } from './AttachmentImage';
import { VoiceTranscript } from './VoiceTranscript';
import { AudioRecorder } from './AudioRecorder';
import { ArtifactInlineBadge } from '@/components/artifacts';
import type { Attachment, SimpleChatMessage, AgentToolCall } from '@/types';

// Artifact tool names that produce an artifact_id in tool_output.
//
// MCP tools arrive in the stream fully-qualified — `mcp__<server>__<tool>`
// (e.g. `mcp__common_tools_module__register_artifact`), NOT the bare name.
// An exact-match Set silently never matched, so artifact tool calls were
// never recognised and the artifact panel only updated on an unrelated
// reload (agent switch). Match the bare suffix instead so both the
// qualified and unqualified forms are recognised.
//
// Pointer model (2026-05-14): the single tool is `register_artifact`; the
// older `create_artifact` / `upload_artifact_file` names are gone — see the
// artifact_runner + artifact_tool mirror md files.
const ARTIFACT_TOOL_BASE_NAMES = ['register_artifact'];

function isArtifactToolName(toolName: string): boolean {
  return ARTIFACT_TOOL_BASE_NAMES.some(
    (base) => toolName === base || toolName.endsWith(`__${base}`),
  );
}

/**
 * Fetch the latest Artifact metadata and upsert into the store, deduped by
 * tool_call_id so we run at most once per emitted tool call.
 *
 * Why always refetch (not just "if missing"): a `register_artifact` call
 * with `target_artifact_id=<existing>` is the agent's refresh signal — same
 * `artifact_id` returns but with a bumped `updated_at`. If we short-circuit
 * on "already in store" the renderers never see the new timestamp and the
 * iframe doesn't reload. Refetching every NEW tool call ensures the
 * downstream `useArtifactRawUrl(refreshKey=updated_at)` keys re-mint and
 * the artifact's iframe / blob reloads with the latest bytes.
 *
 * The seen-Set lives at module scope so the React render loop (which fires
 * this from inside the timeline map) doesn't re-trigger fetches that would
 * race with the upsert and cause an infinite re-render. A tool_call_id is
 * globally unique within an agent session, so collisions across panels
 * don't happen.
 */
const _seenArtifactToolCallIds = new Set<string>();

function refreshArtifactFromToolCall(
  agentId: string,
  artifactId: string,
  toolCallId: string,
): void {
  if (_seenArtifactToolCallIds.has(toolCallId)) return;
  _seenArtifactToolCallIds.add(toolCallId);
  artifactsApi
    .getDetail(agentId, artifactId)
    .then((d) => useArtifactStore.getState().upsert(d))
    .catch(() => undefined);
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
  agentId: string;
  allArtifacts: ReturnType<typeof useArtifactStore.getState>['artifacts'];
}

const ArtifactToolCallCards = memo(function ArtifactToolCallCardsImpl({
  toolCalls, agentId, allArtifacts,
}: ArtifactToolCallCardsProps) {
  // Collect unique artifact_ids in first-seen order across the turn's
  // tool calls. Re-register on the same artifact yields the same id; we
  // still refresh its metadata (via refreshArtifactFromToolCall) but
  // render one badge.
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

    // Refetch fresh metadata for this tool call (deduped per call). Same
    // `artifact_id` from a re-register lands here with a new tool_output
    // string (new token+timestamp), so the dedup key naturally distinguishes
    // first-register vs. subsequent refresh signals.
    const dedupKey = `${tc.step ?? ''}::${tc.tool_output ?? ''}`;
    refreshArtifactFromToolCall(agentId, artifactId, dedupKey);

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
    prev.agentId === next.agentId &&
    prev.toolCalls === next.toolCalls &&
    prev.allArtifacts === next.allArtifacts
  );
});

// Must match BOOTSTRAP_GREETING in src/xyz_agent_context/bootstrap/template.py
const BOOTSTRAP_GREETING =
  "Hi there... I just woke up. Everything feels brand new.\n\n" +
  "I don't have a name yet, and I don't really know who I am " +
  "— but I know you're the one who brought me here.\n\n" +
  "Would you like to tell me what I should be called? " +
  "And what should I call you?";

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
  // Per-agent model/framework panel, opened from the header. A bump on save
  // tells the composer model chip to re-read the (possibly changed) model.
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
  const fileInputRef = useRef<HTMLInputElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

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
    messages,
    currentAssistantMessage: _rtAssistantMessage,
    currentThinking: _rtThinking,
    currentSteps: _rtSteps,
    currentToolCalls: _rtToolCalls,
    currentEvents: _rtEvents,
    isStreaming, addUserMessage, startStreaming,
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
  // Per-agent greeting (Arena etc.) with generic fallback. MUST match the
  // backend-persisted greeting (agent_metadata.bootstrap_greeting), otherwise
  // the instant bubble and the DB greeting differ and both render (dup).
  const bootstrapGreeting = currentAgent?.bootstrap_greeting || BOOTSTRAP_GREETING;

  const { run, reconnect, stop, isLoading } = useAgentWebSocket({
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

  const loadChatHistory = useCallback(async () => {
    if (!agentId || !userId) return;
    setIsLoadingHistory(true);
    try {
      const response = await api.getSimpleChatHistory(agentId, HISTORY_PAGE_SIZE);
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
        agentId, HISTORY_PAGE_SIZE, historyLengthRef.current
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

  // Reload when the agent changes OR a data wipe bumps historyRefreshTick.
  const historyRefreshTick = useChatStore((s) => s.historyRefreshTick);
  useEffect(() => {
    if (agentId && userId) {
      setHistoryMessages([]);
      setHistoryLoaded(false);
      setHistoryTotalCount(0);
      shouldAutoScrollRef.current = true;
      loadChatHistory();
    }
  }, [agentId, userId, loadChatHistory, historyRefreshTick]);

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
        const response = await api.getSimpleChatHistory(agentId, HISTORY_PAGE_SIZE);
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
  // History (DB) + session (chatStore) merged + de-duplicated. The dedup
  // (event_id-first, content heuristic fallback) lives in the pure,
  // unit-tested @/lib/buildTimeline so the logic — burned twice now,
  // Bug 19 and the "latest reply shown twice" bug — is testable in
  // isolation instead of buried in a useMemo.
  const timeline: TimelineItem[] = useMemo(
    () => buildUnifiedTimeline(historyMessages, messages),
    [historyMessages, messages],
  );

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
    [agentId, userId, t],
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
    const trimmed = (composerRef.current?.getText() ?? '').trim();
    const hasContent = trimmed.length > 0 || pendingAttachments.length > 0;
    if (!hasContent || isLoading || !agentId || !userId || uploadingCount > 0) return;

    const content = trimmed;
    const attachmentsToSend = pendingAttachments;
    composerRef.current?.clear();
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
                content: bootstrapGreeting,
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
        'chat-frosted flex flex-col h-full overflow-hidden transition-colors',
        isDragging && 'ring-2 ring-inset ring-[var(--accent-primary)]'
      )}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {/* Header — carbon·silicon binding-dot brand motif + NM mono label.
          Hidden on mobile: the top bar shows the breadcrumb, and the activity
          icon moves into the Chat/Artifacts tab row (see MainLayout). */}
      <div className="px-5 hidden md:flex items-center justify-between border-b min-h-[48px]" style={{ borderColor: 'var(--nm-hairline)' }}>
        {/* min-w-0 + overflow-hidden: when the artifact column squeezes
            the chat, the label/agent-id side TRUNCATES — it must never
            run under the Processing/cost cluster on the right. */}
        <div className="flex items-center gap-2.5 min-w-0 overflow-hidden">
          <BindingDot size={7} pulse={isStreaming} className="shrink-0" />
          <BracketSectionLabel
            trailing={agentId ? <span className="opacity-60 normal-case tracking-normal text-[10px] truncate max-w-[180px]">{agentId}</span> : undefined}
          >
            {t('chat.interaction')}
          </BracketSectionLabel>
        </div>

        <div className="flex items-center gap-3 shrink-0">
          {isStreaming && <ExecutionPopover steps={currentSteps} />}
          {/* Per-agent model & framework settings — sits left of the cost
              chip in the header icon cluster. Opens the detailed panel;
              the composer chip stays the quick model switch. */}
          {agentId && (
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              title="Model & framework (this agent)"
              onClick={() => setAgentCfgOpen(true)}
            >
              <SlidersHorizontal className="h-4 w-4" />
            </Button>
          )}
          {/* Cost chip — a proper header member (it used to float
              absolutely over this corner and collided with the
              Processing indicator during runs). */}
          <span data-help-id="chat.cost">
            <CostPopover />
          </span>
        </div>
      </div>

      {/* Chat tabs — Conversation (owner↔agent direct chat) vs Inner Thoughts
          (the agent's background activity + cross-channel narrations). */}
      <div
        className="px-5 max-md:px-3 flex items-center gap-1 border-b"
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

      {/* Security reminder banner — persistent, non-dismissible. Warns
          users not to paste sensitive personal data into chat (it would be
          stored in trajectories + sent to the LLM provider). Added as part
          of the 2026-06-17 security hardening. */}
      <div
        className="px-5 py-1.5 flex items-center gap-1.5 text-[11px] leading-snug border-b"
        style={{
          borderColor: 'var(--nm-hairline)',
          background: 'var(--bg-tertiary)',
          color: 'var(--color-warning, var(--text-tertiary))',
        }}
      >
        <span aria-hidden="true">⚠</span>
        <span>
          Security reminder: never paste sensitive personal information here —
          wallet addresses, private keys, seed phrases, passwords, or account
          credentials.
        </span>
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
        viewportClassName="p-5 max-md:p-3"
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

        {/* Empty state. With an agent selected → the JourneyBand onboarding
            (binding-dot eyebrow, memory→network→team stations, suggested
            prompts that fill the composer). With no agent → the plain
            bracket prompt to pick one from the sidebar. */}
        {showEmptyState && (
          agentId ? (
            <OnboardingJourney
              agentName={currentAgent?.name || agentId}
              onPrompt={(text) => composerRef.current?.setText(text)}
            />
          ) : (
            <BracketEmptyState
              label="Select an agent"
              hint="Choose an agent from the sidebar to begin your interaction."
            />
          )
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
            />
          </div>
        )}

        {/* Unified timeline */}
        {timeline.map((item) => {
          // What belongs in Inner Thoughts vs Conversation:
          //   - activity records ("Background activity (discord)") — the
          //     lightweight "a background turn happened" markers — are the ONLY
          //     thing routed to Inner Thoughts.
          //   - everything the agent actually *says* is owner-facing and stays
          //     in Conversation. This includes its "I replied to a Discord user
          //     / notified you" narrations: the agent chose to address the owner
          //     via send_message_to_user_directly, so the channel that triggered
          //     the turn (discord / slack / lark / job / …) is irrelevant — it
          //     is still a real message to the owner and must show in the direct
          //     conversation, not be hidden away as an "inner thought".
          // Session items (the owner's live in-app turn) are conversation too.
          const isActivity = item.messageType === 'activity';
          const isInner = isActivity;

          // Route by tab: each tab renders only its own items.
          if (chatTab === 'inner' ? !isInner : isInner) return null;

          // Activity record → source-labelled, expandable card (Inner Thoughts only).
          if (isActivity) {
            return <InnerThoughtCard key={item.id} item={item} agentId={agentId} />;
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
                  timeline: item.timeline,
                  isError: item.isError,
                  warnings: item.warnings,
                  actionReason: item.actionReason,
                }}
                eventId={item.eventId}
                agentId={agentId}
                agentName={currentAgent?.name || agentId}
              />
              {/* Render inline artifact preview cards for register_artifact
                  tool calls that returned an artifact_id */}
              {hasArtifactTools && agentId && item.toolCalls && (
                <ArtifactToolCallCards
                  toolCalls={item.toolCalls}
                  agentId={agentId}
                  allArtifacts={allArtifacts}
                />
              )}
            </div>
          );
        })}

        {/* Inline TurnTimeline — replaces the old "streaming MessageBubble
            + Live activity preview" pair. Renders thinking / tool /
            reply blocks in chronological order as the events arrive,
            so the user can see the agent's actual rhythm. See
            TurnTimeline.tsx and the 2026-05-12 redesign mirror md.

            Wrapped in the same Bot-avatar + flex-1 content shell as
            MessageBubble uses for historical turns, so the in-flight
            turn doesn't visually detach from the rest of the
            conversation (it would otherwise be the only assistant
            output with no left-side avatar). */}
        {chatTab === 'conversation' && isStreaming && currentEvents.length > 0 && (
          <div className="flex gap-3 animate-fade-in">
            <RingAvatar
              species="silicon"
              label={(currentAgent?.name || agentId || 'AI').slice(0, 2)}
              size="sm"
              className="shrink-0"
            />
            <div className="flex-1 min-w-0">
              <TurnTimeline events={currentEvents} isStreaming />
              {/* Mid-stream artifact preview is independent of the timeline:
                  it surfaces created/uploaded artifacts inline as soon as
                  their tool_output lands, without waiting for the whole
                  turn to finish. */}
              {agentId && currentToolCalls.length > 0 && (
                <ArtifactToolCallCards
                  toolCalls={currentToolCalls}
                  agentId={agentId}
                  allArtifacts={allArtifacts}
                />
              )}
              {/* Inter-event "still working" indicator. Reassurance for
                  the gap between two visible blocks (e.g. waiting on a
                  tool result, or the next thinking hasn't started
                  streaming yet) — without it the page goes silent
                  and the user can't tell stuck from busy. Distinct from
                  "Thinking" (whose content is already on screen): this
                  signals the agent is *acting* between the visible
                  blocks. Disappears the instant isStreaming flips. */}
              <div className="mt-3 flex items-center gap-1.5 text-[10px] uppercase tracking-[0.16em] font-mono text-[var(--text-tertiary)]">
                <Loader2 className="w-3 h-3 animate-spin text-[var(--accent-primary)]" />
                <span>Acting…</span>
              </div>
            </div>
          </div>
        )}

        {/* Initial "starting up..." indicator — shown only when streaming
            has started but no event has arrived yet (the timeline is
            empty). As soon as the first thinking / tool / reply event
            comes in, the indicator is replaced by TurnTimeline. Same
            avatar shell as the streaming branch so the layout doesn't
            jump when the first event arrives. */}
        {chatTab === 'conversation' && isStreaming && currentEvents.length === 0 && (() => {
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
          return (
            <div className="flex gap-3 animate-fade-in">
              <RingAvatar
                species="silicon"
                label={(currentAgent?.name || agentId || 'AI').slice(0, 2)}
                size="sm"
                className="shrink-0"
              />
              <div className="flex-1 min-w-0 py-2">
                <div className="flex items-center gap-3">
                  <div className="relative">
                    <Loader2 className="w-5 h-5 text-[var(--accent-primary)] animate-spin" />
                    <div className="absolute inset-0 bg-[var(--accent-primary)] blur-md opacity-30" />
                  </div>
                  <span className="text-sm text-[var(--text-secondary)]">{getInitStatus()}</span>
                </div>
              </div>
            </div>
          );
        })()}

        {/* Scroll anchor. max-md:-mt-4 cancels the space-y-4 margin this empty
            div would otherwise add, killing the dead gap below the last message
            on mobile (combined with the smaller viewport padding below). */}
        <div ref={messagesEndRef} className="max-md:-mt-4" />
      </div>
      </ScrollArea>

      {/* Input area — drop is handled at the Card root, so this wrapper
          no longer needs its own onDragOver/onDragLeave/onDrop. */}
      <div className="px-5 py-4 border-t border-[var(--rule)]">
        {/* Audio transcription unavailable notice — only shown when an
            audio upload returned transcription_available=false. */}
        {transcriptionNotice && (
          <div className="mb-2.5 flex items-start gap-2 rounded-md border border-[var(--rule)] bg-[var(--bg-tertiary)]/60 px-3 py-2 text-xs text-[var(--text-secondary)]">
            <span className="flex-1">{transcriptionNotice}</span>
            <button
              type="button"
              onClick={() => setTranscriptionNotice(null)}
              className="p-0.5 rounded hover:bg-[var(--bg-secondary)]"
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
                  className="relative flex items-center gap-2 rounded-md border border-[var(--rule)] bg-[var(--bg-tertiary)]/60 pr-7 pl-1.5 py-1 max-w-[300px]"
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
        <div className="relative" data-help-id="chat.composer">
          <Composer
            key={agentId ?? '__none__'}
            ref={composerRef}
            agentId={agentId}
            disabled={!agentId}
            placeholder={
              !agentId
                ? t('chat.composer.selectAgentFirst')
                : isDragging
                  ? t('chat.composer.dropFile')
                  : t('chat.composer.placeholder')
            }
            onSubmit={stableSubmit}
            onEmptyChange={setComposerEmpty}
            onDragOver={stableDragOver}
            onDragLeave={stableDragLeave}
            onDrop={stableDrop}
            onPaste={stablePaste}
          />
          {/* Send / Stop — vertically centered at the right edge of the
              textarea. The send (↵) button rests neutral gray and lights up in
              the Carbon (human) palette — carbon-soft fill + carbon border +
              carbon glyph, matching the user's own chat bubble — only once the
              composer has content (text or an attachment), not merely on
              focus/hover. Stop stays oxblood (danger). */}
          {isStreaming ? (
            <Button
              variant="danger"
              size="icon"
              onClick={() => agentId && stop(agentId)}
              className="absolute right-2 top-1/2 -translate-y-1/2 h-9 w-9"
              title="Stop generation"
            >
              <Square className="w-4 h-4 fill-current" />
            </Button>
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

        {/* Tools row — attach (+) and voice on the left; read-only model
            badge on the right (links to Settings — see ComposerModelBadge). */}
        <div className="mt-1 flex items-center justify-between gap-2">
          <div className="flex items-center gap-0.5">
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
          </div>
          <ComposerModelBadge agentId={agentId} reloadKey={modelReloadKey} />
        </div>
      </div>

      {/* Per-agent model & framework panel (opened from the header). */}
      {agentId && (
        <AgentLlmConfigPanel
          agentId={agentId}
          isOpen={agentCfgOpen}
          onClose={() => setAgentCfgOpen(false)}
          onSaved={() => setModelReloadKey((k) => k + 1)}
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
