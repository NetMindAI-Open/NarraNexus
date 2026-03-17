/**
 * Message Bubble component - Bioluminescent Terminal style
 * Distinctive message bubbles with dramatic visual effects
 */

import { User, Bot, ChevronDown, ChevronRight, Wrench, Sparkles, AlertTriangle } from 'lucide-react';
import { useState } from 'react';
import type { ChatMessage } from '@/types';
import { cn, formatTime } from '@/lib/utils';
import { Markdown } from '@/components/ui';

interface MessageBubbleProps {
  message: ChatMessage;
  isStreaming?: boolean;
}

export function MessageBubble({ message, isStreaming = false }: MessageBubbleProps) {
  const [showThinking, setShowThinking] = useState(false);
  const [showTools, setShowTools] = useState(false);
  const isUser = message.role === 'user';

  return (
    <div
      className={cn(
        'flex gap-3',
        isUser && 'flex-row-reverse'
      )}
    >
      {/* Avatar */}
      <div
        className={cn(
          'w-9 h-9 rounded-xl flex items-center justify-center shrink-0 transition-all duration-300',
          isUser
            ? 'bg-[var(--bg-tertiary)] border border-[var(--border-default)] text-[var(--text-secondary)]'
            : 'bg-[var(--gradient-primary)] shadow-[0_0_15px_var(--accent-glow)]'
        )}
      >
        {isUser ? (
          <User className="w-4 h-4" />
        ) : (
          <Bot className="w-4 h-4 text-[var(--text-inverse)] dark:text-[var(--bg-deep)]" />
        )}
      </div>

      {/* Content */}
      <div className={cn('flex-1 min-w-0', isUser && 'text-right')}>
        <div
          className={cn(
            'inline-block max-w-[85%] text-left',
            'px-4 py-3 rounded-2xl',
            'transition-all duration-300',
            isUser
              ? [
                  'message-user',
                  'bg-[var(--gradient-primary)]',
                  'text-[var(--text-inverse)] dark:text-[var(--bg-deep)]',
                  'rounded-tr-md',
                ]
              : message.isError
                ? [
                    'message-assistant',
                    'bg-red-950/30',
                    'text-red-400',
                    'border border-red-500/40',
                    'rounded-tl-md',
                  ]
                : [
                    'message-assistant',
                    'bg-[var(--bg-elevated)]',
                    'text-[var(--text-primary)]',
                    'border border-[var(--border-default)]',
                    'rounded-tl-md',
                  ]
          )}
        >
          {/* Thinking section */}
          {message.thinking && (
            <div className="mb-3 pb-2 border-b border-[var(--border-subtle)]">
              <button
                onClick={() => setShowThinking(!showThinking)}
                className={cn(
                  'flex items-center gap-1.5 text-xs transition-colors',
                  isUser
                    ? 'text-white/70 hover:text-white'
                    : 'text-[var(--text-tertiary)] hover:text-[var(--accent-primary)]'
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
                <div className={cn(
                  'mt-2 p-3 rounded-xl text-xs font-mono whitespace-pre-wrap leading-relaxed',
                  isUser
                    ? 'bg-white/10 text-white/80'
                    : 'bg-[var(--bg-sunken)] text-[var(--text-secondary)] border border-[var(--border-subtle)]'
                )}>
                  {message.thinking}
                </div>
              )}
            </div>
          )}

          {/* Tool calls section */}
          {message.toolCalls && message.toolCalls.length > 0 && (
            <div className="mb-3 pb-2 border-b border-[var(--border-subtle)]">
              <button
                onClick={() => setShowTools(!showTools)}
                className={cn(
                  'flex items-center gap-1.5 text-xs transition-colors',
                  isUser
                    ? 'text-white/70 hover:text-white'
                    : 'text-[var(--text-tertiary)] hover:text-[var(--accent-primary)]'
                )}
              >
                {showTools ? (
                  <ChevronDown className="w-3 h-3" />
                ) : (
                  <ChevronRight className="w-3 h-3" />
                )}
                <Wrench className="w-3 h-3" />
                <span className="font-medium">{message.toolCalls.length} tool call{message.toolCalls.length > 1 ? 's' : ''}</span>
              </button>
              {showTools && (
                <div className="mt-2 space-y-2">
                  {message.toolCalls.map((tool, i) => (
                    <div
                      key={i}
                      className={cn(
                        'p-3 rounded-xl text-xs font-mono',
                        isUser
                          ? 'bg-white/10'
                          : 'bg-[var(--bg-sunken)] border border-[var(--border-subtle)]'
                      )}
                    >
                      <div className={cn(
                        'font-semibold flex items-center gap-1.5',
                        isUser ? 'text-white' : 'text-[var(--accent-primary)]'
                      )}>
                        <span className="w-1.5 h-1.5 rounded-full bg-current" />
                        {tool.tool_name}
                      </div>
                      <div className={cn(
                        'mt-1.5 truncate',
                        isUser ? 'text-white/60' : 'text-[var(--text-tertiary)]'
                      )}>
                        {JSON.stringify(tool.tool_input)}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Message content */}
          <div className={cn(
            'text-sm break-words leading-relaxed',
            message.isError && 'text-red-400'
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

          {/* Non-fatal warnings (e.g., module decision LLM failed but fallback used) */}
          {message.warnings && message.warnings.length > 0 && (
            <div className="mt-2 pt-2 border-t border-amber-500/20">
              {message.warnings.map((warning, i) => (
                <div key={i} className="flex items-start gap-1.5 text-xs text-amber-500">
                  <AlertTriangle className="w-3 h-3 mt-0.5 shrink-0" />
                  <span>{warning}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Timestamp */}
        <div
          className={cn(
            'mt-1.5 text-[10px] text-[var(--text-tertiary)] font-mono tracking-wide',
            isUser ? 'text-right pr-1' : 'text-left pl-1'
          )}
        >
          {formatTime(message.timestamp)}
        </div>
      </div>
    </div>
  );
}
