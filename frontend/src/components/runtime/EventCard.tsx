/**
 * Event Card - Display a single event with all its data
 * Bioluminescent Terminal style - Deep ocean aesthetics
 */

import { useState } from 'react';
import { ChevronDown, ChevronRight, Zap, Clock, Bot, User, Terminal, FileText } from 'lucide-react';
import { Badge, Markdown, ScrollArea } from '@/components/ui';
import type { ChatHistoryEvent } from '@/types';
import { cn, formatTime, truncate } from '@/lib/utils';

interface EventCardProps {
  event: ChatHistoryEvent;
  index: number;
  total: number;
}

export function EventCard({ event, index, total }: EventCardProps) {
  const [isExpanded, setIsExpanded] = useState(false);

  // Extract user input from event_log
  const userInputEntry = event.event_log?.find(
    (entry) => entry.type === 'user_input' || entry.type === 'input' || entry.type === 'trigger'
  );
  const userInput = userInputEntry?.content;

  // Filter event_log for display (exclude user input, show thinking/tool calls)
  const displayLogs = event.event_log?.filter(
    (entry) => !['user_input', 'input', 'trigger'].includes(entry.type)
  ) || [];

  return (
    <div
      className={cn(
        'rounded-xl border overflow-hidden transition-all duration-300',
        isExpanded
          ? 'border-[var(--accent-primary)]/30 bg-[var(--bg-elevated)] shadow-[0_0_15px_var(--accent-glow)]'
          : 'border-[var(--border-subtle)] bg-[var(--bg-sunken)] hover:border-[var(--accent-primary)]/20 hover:bg-[var(--bg-elevated)]'
      )}
    >
      {/* Event Header */}
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full px-3 py-2.5 flex items-center gap-2.5 text-left transition-all duration-300 group"
      >
        <span className={cn(
          'transition-all duration-300',
          isExpanded ? 'text-[var(--accent-primary)]' : 'text-[var(--text-tertiary)]'
        )}>
          {isExpanded ? (
            <ChevronDown className="w-3.5 h-3.5" />
          ) : (
            <ChevronRight className="w-3.5 h-3.5 group-hover:translate-x-0.5 transition-transform" />
          )}
        </span>

        {/* Event number indicator */}
        <span className={cn(
          'w-6 h-6 rounded-lg flex items-center justify-center text-[10px] font-mono shrink-0 transition-all duration-300',
          isExpanded
            ? 'bg-[var(--accent-primary)] text-[var(--bg-deep)]'
            : 'bg-[var(--bg-tertiary)] text-[var(--text-tertiary)] group-hover:bg-[var(--accent-glow)] group-hover:text-[var(--accent-primary)]'
        )}>
          {index}
        </span>

        <div className="flex-1 min-w-0">
          {/* Preview of user input or trigger */}
          <div className="text-xs text-[var(--text-primary)] truncate font-medium group-hover:text-[var(--accent-primary)] transition-colors">
            {typeof userInput === 'string'
              ? truncate(userInput, 60)
              : event.trigger_source || `Event ${index}`}
          </div>

          <div className="flex items-center gap-3 mt-1 text-[10px] text-[var(--text-tertiary)] font-mono">
            <span className="flex items-center gap-1">
              <Clock className="w-2.5 h-2.5" />
              {formatTime(event.created_at)}
            </span>
            <span className="flex items-center gap-1">
              <Zap className="w-2.5 h-2.5 text-[var(--color-warning)]" />
              {event.trigger}
            </span>
          </div>
        </div>

        <Badge variant={isExpanded ? 'accent' : 'default'} size="sm" className="text-[9px] font-mono">
          {displayLogs.length} logs
        </Badge>
      </button>

      {/* Event Details */}
      {isExpanded && (
        <div className="border-t border-[var(--border-subtle)] divide-y divide-[var(--border-subtle)] animate-fade-in">
          {/* User Input Section */}
          {userInput !== undefined && userInput !== null && (
            <div className="p-3">
              <div className="flex items-center gap-1.5 text-[10px] text-[var(--accent-primary)] font-medium uppercase tracking-wider mb-2">
                <User className="w-3 h-3" />
                User Input
              </div>
              <div className="p-3 bg-[var(--bg-sunken)] rounded-lg border border-[var(--border-subtle)] text-xs text-[var(--text-primary)] font-mono">
                {typeof userInput === 'string' ? userInput : JSON.stringify(userInput, null, 2)}
              </div>
            </div>
          )}

          {/* Agent Response Section */}
          <div className="p-3">
            <div className="flex items-center gap-1.5 text-[10px] text-[var(--color-success)] font-medium uppercase tracking-wider mb-2">
              <Bot className="w-3 h-3" />
              Agent Response
            </div>
            <ScrollArea className="max-h-[200px] bg-[var(--bg-sunken)] rounded-lg border border-[var(--border-subtle)]" viewportClassName="p-3">
              <div className="text-xs">
              {event.final_output ? (
                <Markdown content={event.final_output} />
              ) : (
                <span className="text-[var(--text-tertiary)] italic">No response</span>
              )}
              </div>
            </ScrollArea>
          </div>

          {/* Event Log Section */}
          {displayLogs.length > 0 && (
            <div className="p-3">
              <div className="flex items-center gap-1.5 text-[10px] text-[var(--accent-secondary)] font-medium uppercase tracking-wider mb-2">
                <Terminal className="w-3 h-3" />
                Event Log
              </div>
              <ScrollArea className="max-h-[200px]">
                <div className="space-y-1.5">
                {displayLogs.map((entry, i) => (
                  <EventLogEntry key={i} entry={entry} />
                ))}
                </div>
              </ScrollArea>
            </div>
          )}

          {/* Metadata Section */}
          <div className="p-3 bg-[var(--bg-sunken)]/50">
            <div className="flex items-center gap-1.5 text-[10px] text-[var(--text-tertiary)] font-medium uppercase tracking-wider mb-2">
              <FileText className="w-3 h-3" />
              Metadata
            </div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-[10px] font-mono">
              <div>
                <span className="text-[var(--text-tertiary)]">Event ID: </span>
                <span className="text-[var(--accent-primary)]">{truncate(event.event_id, 20)}</span>
              </div>
              <div>
                <span className="text-[var(--text-tertiary)]">Trigger: </span>
                <span className="text-[var(--color-warning)]">{event.trigger}</span>
              </div>
              {event.user_id && (
                <div>
                  <span className="text-[var(--text-tertiary)]">User: </span>
                  <span className="text-[var(--text-secondary)]">{event.user_id}</span>
                </div>
              )}
              <div>
                <span className="text-[var(--text-tertiary)]">Position: </span>
                <span className="text-[var(--text-secondary)]">{index} of {total}</span>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

interface EventLogEntryProps {
  entry: {
    timestamp: string;
    type: string;
    content: unknown;
  };
}

function EventLogEntry({ entry }: EventLogEntryProps) {
  const [isExpanded, setIsExpanded] = useState(false);

  const contentStr = typeof entry.content === 'string'
    ? entry.content
    : JSON.stringify(entry.content, null, 2);

  const isLongContent = contentStr.length > 100;

  // Color coding for different log types
  const getTypeConfig = (type: string) => {
    switch (type.toLowerCase()) {
      case 'thinking':
      case 'thought':
        return { color: 'text-[var(--accent-secondary)]', bg: 'bg-[var(--accent-secondary)]/10', border: 'border-[var(--accent-secondary)]/20' };
      case 'tool_call':
      case 'tool':
        return { color: 'text-[var(--accent-primary)]', bg: 'bg-[var(--accent-glow)]', border: 'border-[var(--accent-primary)]/20' };
      case 'tool_result':
      case 'result':
        return { color: 'text-[var(--color-success)]', bg: 'bg-[var(--color-success)]/10', border: 'border-[var(--color-success)]/20' };
      case 'error':
        return { color: 'text-[var(--color-error)]', bg: 'bg-[var(--color-error)]/10', border: 'border-[var(--color-error)]/20' };
      case 'message_output':
      case 'output':
        return { color: 'text-[var(--accent-primary)]', bg: 'bg-[var(--accent-glow)]', border: 'border-[var(--accent-primary)]/20' };
      default:
        return { color: 'text-[var(--text-tertiary)]', bg: 'bg-[var(--bg-tertiary)]', border: 'border-[var(--border-subtle)]' };
    }
  };

  const typeConfig = getTypeConfig(entry.type);

  return (
    <div className={cn('rounded-lg p-2.5 border', typeConfig.bg, typeConfig.border)}>
      <div className="flex items-start gap-2">
        <span className="text-[9px] text-[var(--text-tertiary)] font-mono shrink-0 mt-0.5">
          {formatTime(entry.timestamp)}
        </span>

        <Badge
          variant="default"
          size="sm"
          className={cn('shrink-0 text-[9px] font-mono', typeConfig.color)}
        >
          {entry.type}
        </Badge>

        <div className="flex-1 min-w-0">
          {isLongContent ? (
            <div>
              <button
                onClick={() => setIsExpanded(!isExpanded)}
                className="text-[10px] text-[var(--text-secondary)] hover:text-[var(--accent-primary)] transition-colors text-left"
              >
                {isExpanded ? (
                  <span className="flex items-center gap-1 text-[var(--accent-primary)]">
                    <ChevronDown className="w-3 h-3" />
                    Collapse
                  </span>
                ) : (
                  <span>
                    {truncate(contentStr, 80)}
                    <span className="text-[var(--accent-primary)] ml-1 font-medium">...more</span>
                  </span>
                )}
              </button>
              {isExpanded && (
                <ScrollArea className="mt-2 max-h-[150px] bg-[var(--bg-sunken)] rounded-lg border border-[var(--border-subtle)]" viewportClassName="p-3">
                  <pre className="text-[10px] text-[var(--text-secondary)] whitespace-pre-wrap break-all font-mono">
                    {contentStr}
                  </pre>
                </ScrollArea>
              )}
            </div>
          ) : (
            <span className="text-[10px] text-[var(--text-secondary)] break-all font-mono">
              {contentStr}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
