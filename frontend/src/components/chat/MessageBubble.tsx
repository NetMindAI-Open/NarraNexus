/**
 * Message Bubble component - Bioluminescent Terminal style
 * Distinctive message bubbles with dramatic visual effects
 *
 * Assistant turns render segment-by-segment (2026-07-30): a turn is
 * still one backend record, but the agent may have spoken m times in
 * it. When the message carries `segments` (cut by lib/segmentTurn at
 * stopStreaming, or computed here after an event-log fetch), each
 * user-facing reply renders with its own collapsible process region —
 * the same segmentation the live view showed, so live and settled
 * views agree by construction.
 *
 * Fallback (older messages / no reply in the turn): message.content
 * renders as one Markdown block, with the process behind the legacy
 * "View reasoning & tools" toggle. Data sources, in preference order:
 *   1. eventLogTimeline (new /event-log endpoint, time-ordered)
 *   2. message.thinking + message.toolCalls (live stream just finished)
 *   3. eventLogThinking + eventLogToolCalls (older backend; grouped)
 */

import { Sparkles, AlertTriangle, AlertCircle, Copy, Download, Check, Loader2, FileText, Image as ImageIcon } from 'lucide-react';
import { Popover, PopoverTrigger, PopoverContent } from '@/components/ui/popover';
import { useState, useCallback, useRef, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import type { Attachment, ChatMessage, Segment, TurnEvent } from '@/types';
import type { EventLogToolCall, EventLogTimelineEntry, EventLogResponse } from '@/types';
import { cn, formatDate, formatTime } from '@/lib/utils';
import { Button, Markdown } from '@/components/ui';
import { RingAvatar } from '@/components/nm';
import { api } from '@/lib/api';
import { segmentTurn, timelineToEvents } from '@/lib/segmentTurn';
import { useConfigStore } from '@/stores';
import { AttachmentImage } from './AttachmentImage';
import { VoiceTranscript } from './VoiceTranscript';
import { TurnTimeline } from './TurnTimeline';
import { SegmentedReply } from './SegmentedReply';

interface MessageBubbleProps {
  message: ChatMessage;
  isStreaming?: boolean;
  eventId?: string;    // For lazy-loading event log from history
  agentId?: string;    // Needed for the event log API call
  agentName?: string;  // Drives the assistant avatar label (matches the sidebar AgentList)
}

export function MessageBubble({ message, isStreaming = false, eventId, agentId, agentName }: MessageBubbleProps) {
  const { t } = useTranslation();
  // Free-tier remedy buttons deep-link into Settings via `?tab=` (added in #211).
  const navigate = useNavigate();
  const [showDetails, setShowDetails] = useState(false);
  const [copied, setCopied] = useState(false);
  const userId = useConfigStore((s) => s.userId);
  // Billing exists only for a NetMind session — the routes 404 for a pure-local
  // username user, and SettingsPage hides the Account pane on the same signal.
  // Gates the balance-exhausted button so it can't deep-link into a blank pane.
  const hasBilling = !!useConfigStore((s) => s.netmindToken);
  const isUser = message.role === 'user';

  // Lazy-loaded event log state
  const [eventLogLoading, setEventLogLoading] = useState(false);
  const [eventLogThinking, setEventLogThinking] = useState<string | null>(null);
  const [eventLogToolCalls, setEventLogToolCalls] = useState<EventLogToolCall[] | null>(null);
  const [eventLogTimeline, setEventLogTimeline] = useState<EventLogTimelineEntry[] | null>(null);
  const eventLogCacheRef = useRef<Map<string, EventLogResponse>>(new Map());

  // Build a unified TurnEvent[] for inline rendering. We deliberately
  // skip "reply" events here — the user-facing reply text lives in
  // message.content and is already rendered as Markdown below, so
  // duplicating it inside the timeline would print the reply twice.
  const inlineEvents: TurnEvent[] = useMemo(() => {
    if (isUser) return [];

    // Path 0 — preferred for just-finished turns: the message was
    // persisted with its live-stream timeline attached (chatStore
    // stopStreaming). Use it as-is so the collapsed bubble for the
    // most-recent assistant turn shows exactly what the user just
    // watched stream, no fetch round trip needed.
    if (message.timeline && message.timeline.length > 0) {
      return message.timeline.filter((e) => e.type !== 'reply');
    }

    const events: TurnEvent[] = [];

    // Path 1 — historical: the backend gave us a time-ordered timeline.
    if (eventLogTimeline && eventLogTimeline.length > 0) {
      eventLogTimeline.forEach((entry, idx) => {
        const id = `tl-${idx}`;
        const ts = idx;
        switch (entry.type) {
          case 'thinking':
            if (entry.content) events.push({ id, ts, type: 'thinking', content: entry.content });
            break;
          case 'tool_call':
            events.push({
              id, ts, type: 'tool_call',
              tool_name: entry.tool_name || 'unknown',
              tool_input: entry.tool_input || {},
              reply_via: entry.reply_via,
            });
            break;
          case 'tool_output':
            events.push({
              id, ts, type: 'tool_output',
              tool_name: entry.tool_name || 'unknown',
              output: entry.tool_output || '',
            });
            break;
          case 'native_output':
            if (entry.content) events.push({ id, ts, type: 'native_output', content: entry.content });
            break;
          // 'reply' intentionally skipped — see comment above.
        }
      });
      return events;
    }

    // Path 2 — live stream: message.thinking + message.toolCalls came
    // in via WebSocket. We don't have per-event ts here, so we put
    // thinking first then the tool calls in array order; this matches
    // the legacy MessageBubble's "all thinking on top" layout but at
    // least removes the separate scrollable inner section.
    // Path 3 (older backend lazy-load) falls into the same shape via
    // eventLogThinking / eventLogToolCalls.
    const t = message.thinking || eventLogThinking;
    const calls = message.toolCalls || eventLogToolCalls;
    let i = 0;
    if (t) {
      events.push({ id: `t-${i}`, ts: i++, type: 'thinking', content: t });
    }
    if (calls) {
      calls.forEach((tc) => {
        events.push({
          id: `tc-${i}`, ts: i++, type: 'tool_call',
          tool_name: tc.tool_name,
          tool_input: tc.tool_input,
        });
        if (tc.tool_output) {
          events.push({
            id: `to-${i}`, ts: i++, type: 'tool_output',
            tool_name: tc.tool_name,
            output: tc.tool_output,
          });
        }
      });
    }
    return events;
  }, [isUser, message.timeline, eventLogTimeline, message.thinking, message.toolCalls, eventLogThinking, eventLogToolCalls]);

  // Segment-mode: the turn renders as the m things the agent actually
  // said, each with its own process region. Only when at least one
  // segment has a reply — a zero-reply turn keeps the legacy path
  // (content is "(Agent decided no response needed)", process behind
  // the global toggle), per the design's "don't special-case" rule.
  const segmentsForRender: Segment[] | null = useMemo(() => {
    if (isUser || message.isError) return null;
    if (message.segments?.some((s) => s.reply)) return message.segments;
    // Historical message: the event-log fetch (triggered by the toggle
    // below) hands us the persisted timeline; the same segmentTurn cut
    // then upgrades the bubble from one joined blob to per-segment
    // rendering — one fetch serves all m segments.
    if (eventLogTimeline && eventLogTimeline.length > 0) {
      const segs = segmentTurn(timelineToEvents(eventLogTimeline));
      if (segs.some((s) => s.reply)) return segs;
    }
    return null;
  }, [isUser, message.isError, message.segments, eventLogTimeline]);

  const hasRealTimeData = !!(message.thinking || message.toolCalls?.length);
  const canLoadEventLog = !isUser && !hasRealTimeData && !!eventId && !!agentId;
  const hasEventLogData =
    eventLogTimeline !== null || eventLogThinking !== null || eventLogToolCalls !== null;

  const loadEventLog = useCallback(async () => {
    if (!eventId || !agentId || eventLogLoading) return;

    // Check cache first
    const cached = eventLogCacheRef.current.get(eventId);
    if (cached) {
      setEventLogThinking(cached.thinking || null);
      setEventLogToolCalls(cached.tool_calls.length > 0 ? cached.tool_calls : null);
      setEventLogTimeline(cached.timeline && cached.timeline.length > 0 ? cached.timeline : null);
      return;
    }

    setEventLogLoading(true);
    try {
      const response = await api.getEventLog(agentId, eventId);
      if (response.success) {
        eventLogCacheRef.current.set(eventId, response);
        setEventLogThinking(response.thinking || null);
        setEventLogToolCalls(response.tool_calls.length > 0 ? response.tool_calls : null);
        setEventLogTimeline(
          response.timeline && response.timeline.length > 0 ? response.timeline : null
        );
      }
    } catch (error) {
      console.error('Failed to load event log:', error);
    } finally {
      setEventLogLoading(false);
    }
  }, [eventId, agentId, eventLogLoading]);

  const handleToggleDetails = useCallback(() => {
    if (inlineEvents.length === 0 && canLoadEventLog && !hasEventLogData) {
      loadEventLog();
    }
    setShowDetails((prev) => !prev);
  }, [inlineEvents.length, canLoadEventLog, hasEventLogData, loadEventLog]);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Fallback for older browsers
      const ta = document.createElement('textarea');
      ta.value = message.content;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  }, [message.content]);

  const handleDownload = useCallback(() => {
    const blob = new Blob([message.content], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `message-${new Date(message.timestamp).toISOString().slice(0, 16).replace(/[:.]/g, '-')}.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, [message.content, message.timestamp]);

  // NM: user = Carbon ring (human), assistant = Silicon ring (AI).
  // Assistant avatar mirrors the sidebar AgentList: first 2 chars of the
  // agent name (falling back to 'AI' only when no name is available),
  // instead of a hardcoded 'A'.
  const avatarLabel = isUser
    ? (userId || 'U').slice(0, 1)
    : (message.role === 'assistant' ? (agentName?.slice(0, 2) || 'AI') : '?');

  return (
    <div
      className={cn(
        'flex gap-3',
        isUser && 'flex-row-reverse'
      )}
    >
      {/* NM RingAvatar — carbon for human, silicon for AI. Hidden on mobile
          (both sides) to give the bubbles the full width; the species color on
          the bubble itself still distinguishes who's speaking. */}
      <RingAvatar
        species={isUser ? 'carbon' : 'silicon'}
        label={avatarLabel}
        size="sm"
        className="shrink-0 hidden md:inline-flex"
      />

      {/* Content */}
      <div className={cn('flex-1 min-w-0', isUser && 'text-right')}>
        <div
          className={cn(
            'relative inline-block max-w-[85%] text-left',
            'px-3.5 py-2.5',
            'rounded-[var(--radius-lg)]',
            'transition-colors duration-150',
            // AI (silicon) bubble: rebind markdown code/table fills to a blue
            // tint so they don't read as muddy gray on the blue surface.
            !isUser && !message.isError && 'nm-bubble-ai',
          )}
          style={
            isUser
              ? {
                  // Own bubble — Carbon (human) species variant, matching
                  // the Narra Agent App design ref: carbon-soft coral fill,
                  // carbon-hair border, and a 3px solid carbon stripe on the
                  // RIGHT (the "own" side). This mirrors the AI bubble's
                  // silicon-on-the-LEFT treatment, so a conversation reads as
                  // a clear human(carbon)·AI(silicon) dialogue. Both tints
                  // flip automatically in dark mode via token redefinition.
                  background: 'var(--color-carbon-soft)',
                  color: 'var(--nm-ink)',
                  border: '1px solid var(--color-carbon-hair)',
                  borderRight: '3px solid var(--color-carbon)',
                }
              : message.isError
                ? {
                    background: 'var(--color-error)',
                    color: 'white',
                    border: '1px solid var(--color-error)',
                  }
                : {
                    // AI bubble — NM canonical FinBubble: silicon-soft fill,
                    // silicon-hair border, 3px silicon stripe on the LEFT
                    // edge. Light mode lands on light-blue bg + dark-blue
                    // stripe; dark mode flips to grayish-blue bg + light-blue
                    // stripe (driven entirely by token redefinition).
                    background: 'var(--color-silicon-soft)',
                    color: 'var(--nm-ink)',
                    border: '1px solid var(--color-silicon-hair)',
                    borderLeft: '3px solid var(--color-silicon)',
                  }
          }
        >
          {/* Red error badge — any error surfaces here, whether the whole
              turn failed (isError: no reply / silent fallback / login
              expired, content IS the error text) or the reply came through
              but something errored on the way (warnings). Click to read the
              situation + the error detail. Sits at the bubble's top corner. */}
          {(message.isError || (message.warnings && message.warnings.length > 0)) && (
            <Popover>
              <PopoverTrigger asChild>
                <button
                  type="button"
                  aria-label={t('chat.error.badgeLabel')}
                  className="absolute -top-2 -right-2 z-10 flex items-center justify-center w-5 h-5 rounded-full shadow-sm"
                  style={{ background: 'var(--color-error)', color: 'white' }}
                >
                  <AlertCircle className="w-3.5 h-3.5" />
                </button>
              </PopoverTrigger>
              <PopoverContent side="top" align="end" className="w-[300px] p-3">
                {message.actionReason ? (
                  <>
                    {/* Actionable failure: show localized "what you can do"
                        guidance instead of a generic "turn failed", with the
                        raw detail below. Platform-side executor-infra failures
                        (OOM / unreachable — reasons starting with "executor_",
                        or the generic "infra_transient") get a distinct
                        "execution environment" title vs the user-config
                        "Action needed" one, because the remedy differs
                        (retry / split the task, not change a setting). */}
                    <div className="text-xs font-semibold mb-1" style={{ color: 'var(--color-error)' }}>
                      {t(
                        message.actionReason === 'infra_transient' ||
                          message.actionReason.startsWith('executor_')
                          ? 'chat.error.titleInfra'
                          : 'chat.error.titleActionable'
                      )}
                    </div>
                    <div className="text-xs mb-2" style={{ color: 'var(--text-secondary)' }}>
                      {t(`chat.error.action.${message.actionReason}`, {
                        defaultValue: t('chat.error.action.generic'),
                      })}
                    </div>
                  </>
                ) : (
                  <>
                    <div className="text-xs font-semibold mb-1" style={{ color: 'var(--color-error)' }}>
                      {message.isError ? t('chat.error.titleFailed') : t('chat.error.titleRecovered')}
                    </div>
                    <div className="text-xs mb-2" style={{ color: 'var(--text-secondary)' }}>
                      {message.isError ? t('chat.error.situationFailed') : t('chat.error.situationRecovered')}
                    </div>
                  </>
                )}
                <div
                  className="whitespace-pre-wrap break-words font-[family-name:var(--font-mono)] text-[11px] max-h-40 overflow-y-auto"
                  style={{ color: 'var(--text-tertiary)' }}
                >
                  {message.warnings && message.warnings.length > 0
                    ? message.warnings.join('\n\n')
                    : message.content}
                </div>
              </PopoverContent>
            </Popover>
          )}

          {/* Inline timeline (reasoning + tool calls + tool output)
              for assistant messages. Renders only when expanded; the
              user clicks the affordance below to reveal. Two cases:
                - Real-time data already present → ready to render
                - Historical message → fetch event log first then render
              Either way the result flows through TurnTimeline so a
              "think → tool → think → tool" rhythm survives history,
              matching the live streaming UX. No inner ScrollArea —
              long content pushes the bubble taller and scrolls with
              the main message list (no double-scroll). */}
          {segmentsForRender === null && (inlineEvents.length > 0 || canLoadEventLog) && (
            <div className="mb-3 pb-2 border-b border-[var(--border-subtle)]">
              <button
                onClick={handleToggleDetails}
                disabled={eventLogLoading}
                className={cn(
                  'flex items-center gap-1.5 text-xs transition-colors',
                  'text-[var(--text-tertiary)] hover:text-[var(--text-primary)]'
                )}
              >
                {eventLogLoading ? (
                  <Loader2 className="w-3 h-3 animate-spin" />
                ) : (
                  <Sparkles className="w-3 h-3" />
                )}
                <span className="font-medium">
                  {eventLogLoading
                    ? t('chat.message.loadingDetails')
                    : showDetails
                      ? t('chat.message.hideReasoning')
                      : t('chat.message.viewReasoning')}
                </span>
              </button>
              {showDetails && inlineEvents.length > 0 && (
                <div className="mt-3">
                  <TurnTimeline events={inlineEvents} />
                </div>
              )}
            </div>
          )}

          {/* Attachments — rendered above text content. Three render
              paths, picked in priority order:
                1. Voice memo (att.source === 'recording'): VoiceTranscript
                   renders the transcribed text only — no audio playback.
                   The transcript itself IS the message; the audio bytes
                   are kept on disk for storage but never re-surfaced.
                2. Image (category=image): AttachmentImage loads via the
                   /raw endpoint through an authed fetch + blob URL.
                3. Everything else (including audio FILE uploads via
                   Paperclip / drag-drop, even when transcribed):
                   a generic file chip — the user shared a file with
                   the agent, the transcript still flows to the agent
                   via the system prompt but is not surfaced in the UI. */}
          {message.attachments && message.attachments.length > 0 && (
            <div className="mb-2 flex flex-wrap gap-2">
              {message.attachments.map((att: Attachment) => {
                const haveCreds = !!agentId && !!userId;
                const isVoiceMemo = att.source === 'recording';
                const isImage = att.category === 'image' && haveCreds;
                if (isVoiceMemo) {
                  return (
                    <VoiceTranscript
                      key={att.file_id}
                      transcript={att.transcript}
                    />
                  );
                }
                if (isImage) {
                  return (
                    <AttachmentImage
                      key={att.file_id}
                      agentId={agentId!}
                      userId={userId!}
                      fileId={att.file_id}
                      alt={att.original_name}
                      className="max-h-48 max-w-[280px] rounded border border-[var(--rule)] object-cover"
                      zoomable
                    />
                  );
                }
                return (
                  <div
                    key={att.file_id}
                    className="flex items-center gap-2 rounded-md border border-[var(--rule)] bg-[var(--bg-tertiary)]/40 px-2 py-1.5 max-w-[280px]"
                  >
                    <div className="w-8 h-8 rounded bg-[var(--bg-secondary)] flex items-center justify-center shrink-0">
                      {att.category === 'image' ? (
                        <ImageIcon className="w-4 h-4 text-[var(--text-tertiary)]" />
                      ) : (
                        <FileText className="w-4 h-4 text-[var(--text-tertiary)]" />
                      )}
                    </div>
                    <div className="min-w-0 leading-tight">
                      <div className="text-xs truncate">{att.original_name}</div>
                      <div className="text-[10px] text-[var(--text-tertiary)] font-mono uppercase tracking-[0.1em]">
                        {att.category} · {Math.max(1, Math.round(att.size_bytes / 1024))} KB
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* Message content. NB: an isError bubble already carries a solid
              red background + white text on the container (see style above),
              so we must NOT re-tint the text red here — red-on-red renders as
              an empty red box (the message text vanishes). Let it inherit
              the container's white. */}
          <div className="text-sm break-words leading-relaxed">
            {segmentsForRender ? (
              // Segment mode: the m things the agent said, each with its
              // own collapsible process. Replaces the single content blob
              // — rendering both would print every sentence twice.
              // defaultOpen when the segments came from the event-log
              // fetch: the user already clicked "View reasoning" once to
              // get here, so the process shows immediately instead of
              // behind a second toggle.
              <SegmentedReply
                segments={segmentsForRender}
                showProcess
                defaultOpen={!message.segments?.some((s) => s.reply)}
              />
            ) : isUser ? (
              // Match the Agent reply's font size: the Markdown wrapper
              // (.markdown-content) renders at 0.95rem, but a plain user span
              // would inherit the parent .text-sm (0.85rem) and look smaller.
              // Pin it so both bubbles read at the same size — a notch smaller
              // on mobile, in step with the markdown mobile size.
              <span className="whitespace-pre-wrap text-[0.875rem] md:text-[0.95rem]">{message.content}</span>
            ) : message.actionReason ? (
              // Self-serviceable failure: show a clean, localized "what you
              // can do" line in the body. The full (English) provider detail
              // stays in the badge popover so the body isn't a raw error blob.
              <span className="whitespace-pre-wrap">
                {t(`chat.error.action.${message.actionReason}`, {
                  defaultValue: t('chat.error.action.generic'),
                })}
              </span>
            ) : message.isError ? (
              <span className="whitespace-pre-wrap">{message.content}</span>
            ) : (
              <Markdown content={message.content} />
            )}
            {isStreaming && (
              <span className="inline-block w-0.5 h-4 ml-0.5 bg-[var(--accent-primary)] animate-pulse rounded-full" />
            )}
          </div>

          {/* Non-fatal warnings */}
          {message.warnings && message.warnings.length > 0 && (
            <div className="mt-2 pt-2 border-t border-[var(--rule)]">
              {message.warnings.map((warning, i) => (
                <div key={i} className="flex items-start gap-1.5 text-xs text-[var(--color-yellow-500)]">
                  <AlertTriangle className="w-3 h-3 mt-0.5 shrink-0" />
                  <span>{warning}</span>
                </div>
              ))}
            </div>
          )}

        </div>

        {/* Free-tier exhaustion is the one reason whose remedy is not a setting
            the user can guess at: the wallet cannot be topped up from their side
            and its key was never theirs. So the two paths that DO exist are
            offered as buttons. Before the free tier became an ordinary provider
            card (2026-07-28) this funnel lived in a global HTTP-402 banner; the
            402 disappeared with the pre-run quota gate and took the funnel with
            it.

            OUTSIDE the bubble, like the meta row below and for the same kind of
            reason. Inside, the buttons sit on the solid --color-error fill,
            where every control token in the app misreads: --accent-primary is
            an ink block in light mode and CREAM in dark (a white label on it
            vanishes), --border-default is 22% ink and disappears on red, and a
            filled control cannot share a label color with an outlined one, so
            the pair stops looking like a pair. Three rounds of --on-error
            special-case tokens each fixed one of those and exposed the next.
            On paper the ordinary Button variants just work — accent + outline
            IS the app's primary/secondary pair — so the special case is gone. */}
        {message.actionReason === 'free_tier_exhausted' && (
          <div className={cn('mt-2 flex flex-wrap gap-2', isUser && 'justify-end')}>
            <Button
              variant="accent"
              // /pay, not the account page: it mints the checkout session and
              // redirects to Stripe in one hop (#223), and every degenerate case
              // it handles — already subscribed, desktop webview, non-Power
              // session, 401 — falls back to exactly the account page this used
              // to point at. So the settings detour buys nothing.
              onClick={() => navigate('/pay')}
              className="h-8 px-3 text-xs"
            >
              {t('chat.error.upgradeCta', 'Get Nexus Pro')}
            </Button>
            <Button
              variant="outline"
              onClick={() => navigate('/app/settings?tab=providers')}
              className="h-8 px-3 text-xs"
            >
              {t('chat.error.freeTier.useOwnKey', 'Use my own provider')}
            </Button>
          </div>
        )}

        {/* A spent BALANCE gets its own pair: the plan, and a top-up.

            It shares the plan button with the free-tier funnel but NOT "use my
            own provider" — that one's whole premise is a user who has no card
            of their own, which is the opposite of this user.

            The second label names our DESTINATION rather than an action on
            "your account", because `insufficient_balance` is provider-agnostic
            by design — it fires for a DeepSeek 402, an OpenAI quota, an
            Anthropic credit balance and the user's own NetMind account alike,
            and nothing in the reason says which one ran dry
            (get_provider_source is coarse: providers/resolver.py returns "user"
            for every non-platform card). "Plans & credits" is true in all of
            those cases; "Top up your account" would not be. The bubble text
            still carries the full remedy, including switching providers, so
            this adds shortcuts without narrowing the advice. */}
        {message.actionReason === 'insufficient_balance' && hasBilling && (
          <div className={cn('mt-2 flex flex-wrap gap-2', isUser && 'justify-end')}>
            {/* The plan leads because it is the only remedy that also removes
                the next interruption. Shared with the free-tier funnel on
                purpose — same route, same words, so ONE key
                (`chat.error.upgradeCta`) rather than two that drift: within a
                single PR the duplicated pair had already diverged in 4 of 10
                locales. /pay degrades to the account page for someone already
                subscribed, so the label being off for them costs a redirect,
                not a dead end. */}
            <Button
              variant="accent"
              onClick={() => navigate('/pay')}
              className="h-8 px-3 text-xs"
            >
              {t('chat.error.upgradeCta', 'Get Nexus Pro')}
            </Button>
            <Button
              variant="outline"
              onClick={() => navigate('/app/settings?tab=account')}
              className="h-8 px-3 text-xs"
            >
              {t('chat.error.balance.plans', 'Plans & credits')}
            </Button>
          </div>
        )}

        {/* Meta row — pulled OUTSIDE the bubble so the bubble stays tight
            (no internal footer padding/whitespace). Time + copy/download sit
            just below the bubble, aligned to the bubble's side: right for own
            (carbon) messages, left for agent (silicon) messages. Mono 9.5px
            in the subtle token. */}
        <div
          className={cn(
            'mt-1 flex items-center gap-1.5 px-0.5',
            isUser ? 'justify-end' : 'justify-start'
          )}
        >
          {!isUser && !isStreaming && message.content && (
            <>
              <button
                onClick={handleCopy}
                className="p-0.5 rounded opacity-40 hover:opacity-100 hover:bg-[var(--nm-paper-warm)] transition-all"
                title={t('chat.message.copyMarkdown')}
              >
                {copied ? (
                  <Check className="w-3 h-3 text-[var(--color-success)]" />
                ) : (
                  <Copy className="w-3 h-3" />
                )}
              </button>
              <button
                onClick={handleDownload}
                className="p-0.5 rounded opacity-40 hover:opacity-100 hover:bg-[var(--nm-paper-warm)] transition-all"
                title={t('chat.message.downloadMd')}
              >
                <Download className="w-3 h-3" />
              </button>
            </>
          )}
          <span
            className="font-mono tracking-wide"
            // Hovering the HH:mm:ss reveals the full date — day context for
            // a single message without waiting for a day separator.
            title={`${formatDate(message.timestamp)} ${formatTime(message.timestamp)}`}
            style={{
              color: 'var(--nm-subtle)',
              fontSize: '9.5px',
              letterSpacing: '0.05em',
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            {formatTime(message.timestamp)}
          </span>
        </div>
      </div>
    </div>
  );
}


