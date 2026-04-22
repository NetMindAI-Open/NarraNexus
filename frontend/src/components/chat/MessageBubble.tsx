/**
 * Message Bubble component - Bioluminescent Terminal style
 * Distinctive message bubbles with dramatic visual effects
 *
 * Supports two data sources for thinking/tool calls:
 * 1. Real-time: via message.thinking / message.toolCalls (from WebSocket streaming)
 * 2. History: lazy-loaded via event_id → GET /event-log/{event_id} (on-demand)
 */

import { User, Bot, ChevronDown, ChevronRight, Wrench, Sparkles, AlertTriangle, Copy, Download, Check, Loader2 } from 'lucide-react';
import { useState, useCallback, useRef } from 'react';
import type { ChatMessage } from '@/types';
import type { EventLogToolCall, EventLogResponse } from '@/types';
import { cn, formatTime } from '@/lib/utils';
import { Markdown } from '@/components/ui';
import { api } from '@/lib/api';

interface MessageBubbleProps {
  message: ChatMessage;
  isStreaming?: boolean;
  eventId?: string;    // For lazy-loading event log from history
  agentId?: string;    // Needed for the event log API call
}

export function MessageBubble({ message, isStreaming = false, eventId, agentId }: MessageBubbleProps) {
  const [showThinking, setShowThinking] = useState(false);
  const [showTools, setShowTools] = useState(false);
  const [copied, setCopied] = useState(false);
  const isUser = message.role === 'user';

  // Lazy-loaded event log state
  const [eventLogLoading, setEventLogLoading] = useState(false);
  const [eventLogThinking, setEventLogThinking] = useState<string | null>(null);
  const [eventLogToolCalls, setEventLogToolCalls] = useState<EventLogToolCall[] | null>(null);
  const eventLogCacheRef = useRef<Map<string, EventLogResponse>>(new Map());

  // Determine data source: real-time (message fields) or history (event log)
  const thinking = message.thinking || eventLogThinking;
  const toolCalls = message.toolCalls || eventLogToolCalls;
  const hasRealTimeData = !!(message.thinking || message.toolCalls?.length);
  const canLoadEventLog = !isUser && !hasRealTimeData && !!eventId && !!agentId;
  const hasEventLogData = eventLogThinking !== null || eventLogToolCalls !== null;

  const loadEventLog = useCallback(async () => {
    if (!eventId || !agentId || eventLogLoading) return;

    // Check cache first
    const cached = eventLogCacheRef.current.get(eventId);
    if (cached) {
      setEventLogThinking(cached.thinking || null);
      setEventLogToolCalls(cached.tool_calls.length > 0 ? cached.tool_calls : null);
      return;
    }

    setEventLogLoading(true);
    try {
      const response = await api.getEventLog(agentId, eventId);
      if (response.success) {
        eventLogCacheRef.current.set(eventId, response);
        setEventLogThinking(response.thinking || null);
        setEventLogToolCalls(response.tool_calls.length > 0 ? response.tool_calls : null);
      }
    } catch (error) {
      console.error('Failed to load event log:', error);
    } finally {
      setEventLogLoading(false);
    }
  }, [eventId, agentId, eventLogLoading]);

  const handleToggleThinking = useCallback(() => {
    if (!thinking && canLoadEventLog && !hasEventLogData) {
      loadEventLog();
    }
    setShowThinking((prev) => !prev);
  }, [thinking, canLoadEventLog, hasEventLogData, loadEventLog]);

  const handleToggleTools = useCallback(() => {
    if (!toolCalls && canLoadEventLog && !hasEventLogData) {
      loadEventLog();
    }
    setShowTools((prev) => !prev);
  }, [toolCalls, canLoadEventLog, hasEventLogData, loadEventLog]);

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

  // Whether to show the "Load details" button for history messages
  const showLoadDetailsButton = canLoadEventLog && !hasEventLogData;

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
            ? 'bg-[var(--bg-tertiary)] border border-[var(--rule)] text-[var(--text-secondary)]'
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
          {/* Thinking section (real-time or lazy-loaded) */}
          {(thinking || (showThinking && canLoadEventLog)) && (
            <div className="mb-3 pb-2 border-b border-[var(--border-subtle)]">
              <button
                onClick={handleToggleThinking}
                className={cn(
                  'flex items-center gap-1.5 text-xs transition-colors',
                  isUser
                    ? 'opacity-70 hover:opacity-100'
                    : 'text-[var(--text-tertiary)] hover:text-[var(--text-primary)]'
                )}
              >
                {showThinking ? (
                  <ChevronDown className="w-3 h-3" />
                ) : (
                  <ChevronRight className="w-3 h-3" />
                )}
                <Sparkles className="w-3 h-3" />
                <span className="font-medium">Reasoning</span>
              </button>
              {showThinking && (
                eventLogLoading ? (
                  <div className="mt-2 flex items-center gap-2 text-xs text-[var(--text-tertiary)]">
                    <Loader2 className="w-3 h-3 animate-spin" />
                    <span>Loading...</span>
                  </div>
                ) : thinking ? (
                  <div className={cn(
                    'mt-2 p-3 text-xs font-mono whitespace-pre-wrap leading-relaxed max-h-[300px] overflow-y-auto',
                    isUser
                      ? 'bg-[rgb(255_255_255_/_0.1)] opacity-80 dark:bg-[rgb(17_18_20_/_0.08)]'
                      : 'bg-[var(--bg-secondary)] text-[var(--text-secondary)] border border-[var(--rule)]'
                  )}>
                    {thinking}
                  </div>
                ) : null
              )}
            </div>
          )}

          {/* Tool calls section (real-time or lazy-loaded) */}
          {(toolCalls && toolCalls.length > 0 || (showTools && canLoadEventLog)) && (
            <div className="mb-3 pb-2 border-b border-[var(--border-subtle)]">
              <button
                onClick={handleToggleTools}
                className={cn(
                  'flex items-center gap-1.5 text-xs transition-colors',
                  isUser
                    ? 'opacity-70 hover:opacity-100'
                    : 'text-[var(--text-tertiary)] hover:text-[var(--text-primary)]'
                )}
              >
                {showTools ? (
                  <ChevronDown className="w-3 h-3" />
                ) : (
                  <ChevronRight className="w-3 h-3" />
                )}
                <Wrench className="w-3 h-3" />
                <span className="font-medium">
                  {toolCalls && toolCalls.length > 0
                    ? `${toolCalls.length} tool call${toolCalls.length > 1 ? 's' : ''}`
                    : 'Tool calls'}
                </span>
              </button>
              {showTools && (
                eventLogLoading ? (
                  <div className="mt-2 flex items-center gap-2 text-xs text-[var(--text-tertiary)]">
                    <Loader2 className="w-3 h-3 animate-spin" />
                    <span>Loading...</span>
                  </div>
                ) : toolCalls && toolCalls.length > 0 ? (
                  <div className="mt-2 space-y-2">
                    {toolCalls.map((tool, i) => (
                      <ToolCallItem key={i} tool={tool} isUser={isUser} />
                    ))}
                  </div>
                ) : null
              )}
            </div>
          )}

          {/* "Load details" button for history messages without data yet */}
          {showLoadDetailsButton && (
            <div className="mb-3 pb-2 border-b border-[var(--border-subtle)]">
              <button
                onClick={() => {
                  loadEventLog();
                  setShowThinking(true);
                  setShowTools(true);
                }}
                disabled={eventLogLoading}
                className={cn(
                  'flex items-center gap-1.5 text-xs transition-colors',
                  'text-[var(--text-tertiary)] hover:text-[var(--accent-primary)]'
                )}
              >
                {eventLogLoading ? (
                  <Loader2 className="w-3 h-3 animate-spin" />
                ) : (
                  <Sparkles className="w-3 h-3" />
                )}
                <span className="font-medium">
                  {eventLogLoading ? 'Loading details...' : 'View reasoning & tools'}
                </span>
              </button>
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


/**
 * Tool call display item - supports both real-time AgentToolCall and history EventLogToolCall
 * Shows tool name + truncated input, with expandable full view
 */
function ToolCallItem({ tool, isUser }: { tool: { tool_name: string; tool_input: Record<string, unknown>; tool_output?: string | null }; isUser: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const inputStr = JSON.stringify(tool.tool_input);
  const isLong = inputStr.length > 120;

  return (
    <div
      className={cn(
        'p-3 text-xs font-mono',
        // Inside a user bubble: subtle inset using currentColor so we auto-invert in dark mode.
        // Outside: normal secondary background.
        isUser
          ? 'bg-[color-mix(in_srgb,currentColor_10%,transparent)]'
          : 'bg-[var(--bg-secondary)] border border-[var(--rule)]'
      )}
    >
      <div className={cn(
        'font-semibold flex items-center gap-1.5',
        isUser ? '' : 'text-[var(--text-primary)]'
      )}>
        <span className="w-1.5 h-1.5 rounded-full allow-circle bg-current" />
        {tool.tool_name}
      </div>

      {/* Input */}
      <div className={cn(
        'mt-1.5',
        isUser ? 'opacity-60' : 'text-[var(--text-tertiary)]'
      )}>
        {expanded || !isLong ? (
          <pre className="whitespace-pre-wrap break-all max-h-[200px] overflow-y-auto">
            {JSON.stringify(tool.tool_input, null, 2)}
          </pre>
        ) : (
          <span className="truncate block">{inputStr.slice(0, 120)}...</span>
        )}
        {isLong && (
          <button
            onClick={() => setExpanded((prev) => !prev)}
            className={cn(
              'mt-1 text-[10px] underline hover:no-underline',
              isUser ? 'opacity-80 hover:opacity-100' : 'text-[var(--text-primary)]'
            )}
          >
            {expanded ? 'Collapse' : 'Expand'}
          </button>
        )}
      </div>

      {/* Output (only available for history event log tool calls) */}
      {tool.tool_output && (
        <ToolCallOutput output={tool.tool_output} isUser={isUser} />
      )}
    </div>
  );
}


/**
 * Tool call output - truncated by default, expandable
 */
function ToolCallOutput({ output, isUser }: { output: string; isUser: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const isLong = output.length > 200;

  return (
    <div className={cn(
      'mt-2 pt-2 border-t',
      isUser ? 'border-[color-mix(in_srgb,currentColor_15%,transparent)]' : 'border-[var(--rule)]'
    )}>
      <div className={cn(
        'text-[10px] font-semibold mb-1',
        isUser ? 'opacity-50' : 'text-[var(--text-tertiary)]'
      )}>
        Output
      </div>
      <div className={cn(
        'whitespace-pre-wrap break-all',
        isUser ? 'opacity-50' : 'text-[var(--text-tertiary)]',
        !expanded && isLong && 'max-h-[60px] overflow-hidden relative'
      )}>
        {expanded || !isLong ? output : output.slice(0, 200) + '...'}
      </div>
      {isLong && (
        <button
          onClick={() => setExpanded((prev) => !prev)}
          className={cn(
            'mt-1 text-[10px] underline hover:no-underline',
            isUser ? 'opacity-80 hover:opacity-100' : 'text-[var(--text-primary)]'
          )}
        >
          {expanded ? 'Collapse' : 'Show full output'}
        </button>
      )}
    </div>
  );
}
