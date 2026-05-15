/**
 * Message Bubble component - Bioluminescent Terminal style
 * Distinctive message bubbles with dramatic visual effects
 *
 * History display now matches the live streaming UX: thinking +
 * tool_call + tool_output are rendered inline through TurnTimeline in
 * their original chronological order, not grouped into "Reasoning" /
 * "Tool calls" sections that lost time information and forced double
 * scrolling. Data sources, in preference order:
 *   1. eventLogTimeline (new /event-log endpoint, time-ordered)
 *   2. message.thinking + message.toolCalls (live stream just finished)
 *   3. eventLogThinking + eventLogToolCalls (older backend; grouped)
 */

import { User, Bot, Sparkles, AlertTriangle, Copy, Download, Check, Loader2, FileText, Image as ImageIcon } from 'lucide-react';
import { useState, useCallback, useRef, useMemo } from 'react';
import type { Attachment, ChatMessage, TurnEvent } from '@/types';
import type { EventLogToolCall, EventLogTimelineEntry, EventLogResponse } from '@/types';
import { cn, formatTime } from '@/lib/utils';
import { Markdown } from '@/components/ui';
import { api } from '@/lib/api';
import { useConfigStore } from '@/stores';
import { AttachmentImage } from './AttachmentImage';
import { VoiceTranscript } from './VoiceTranscript';
import { TurnTimeline } from './TurnTimeline';

interface MessageBubbleProps {
  message: ChatMessage;
  isStreaming?: boolean;
  eventId?: string;    // For lazy-loading event log from history
  agentId?: string;    // Needed for the event log API call
}

export function MessageBubble({ message, isStreaming = false, eventId, agentId }: MessageBubbleProps) {
  const [showDetails, setShowDetails] = useState(false);
  const [copied, setCopied] = useState(false);
  const userId = useConfigStore((s) => s.userId);
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

  return (
    <div
      className={cn(
        'flex gap-3',
        isUser && 'flex-row-reverse'
      )}
    >
      {/* Avatar — flat archive square */}
      <div
        className={cn(
          'w-8 h-8 flex items-center justify-center shrink-0 transition-colors duration-150',
          isUser
            ? 'bg-[var(--bg-tertiary)] border border-[var(--border-subtle)] text-[var(--text-secondary)]'
            // Bot avatar uses text-primary → bg-inverse so it inverts automatically.
            : 'bg-[var(--text-primary)] text-[var(--text-inverse)]'
        )}
      >
        {isUser ? (
          <User className="w-3.5 h-3.5" />
        ) : (
          <Bot className="w-3.5 h-3.5" />
        )}
      </div>

      {/* Content */}
      <div className={cn('flex-1 min-w-0', isUser && 'text-right')}>
        <div
          className={cn(
            'inline-block max-w-[85%] text-left',
            'px-4 py-3',
            'transition-colors duration-150',
            isUser
              ? [
                  'message-user',
                ]
              : message.isError
                ? [
                    'message-assistant',
                    'bg-[var(--bg-primary)]',
                    'text-[var(--color-red-500)]',
                    'border border-[var(--color-red-500)]',
                  ]
                : [
                    'message-assistant',
                  ]
          )}
        >
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
          {(inlineEvents.length > 0 || canLoadEventLog) && (
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
                    ? 'Loading details...'
                    : showDetails
                      ? 'Hide reasoning & tools'
                      : 'View reasoning & tools'}
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

          {/* Message content */}
          <div className={cn(
            'text-sm break-words leading-relaxed',
            message.isError && 'text-[var(--color-red-500)]'
          )}>
            {isUser ? (
              <span className="whitespace-pre-wrap">{message.content}</span>
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

        {/* Footer: timestamp + action buttons */}
        <div
          className={cn(
            'mt-1.5 flex items-center gap-2 text-[10px] text-[var(--text-tertiary)] font-mono tracking-wide',
            isUser ? 'justify-end pr-1' : 'justify-start pl-1'
          )}
        >
          <span>{formatTime(message.timestamp)}</span>

          {/* Copy & Download (assistant messages only, not during streaming) */}
          {!isUser && !isStreaming && message.content && (
            <div className="flex items-center gap-1">
              <button
                onClick={handleCopy}
                className="p-0.5 rounded opacity-40 hover:opacity-100 hover:bg-[var(--bg-tertiary)] transition-all"
                title="Copy Markdown"
              >
                {copied ? (
                  <Check className="w-3 h-3 text-[var(--color-success)]" />
                ) : (
                  <Copy className="w-3 h-3" />
                )}
              </button>
              <button
                onClick={handleDownload}
                className="p-0.5 rounded opacity-40 hover:opacity-100 hover:bg-[var(--bg-tertiary)] transition-all"
                title="Download as .md"
              >
                <Download className="w-3 h-3" />
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}


