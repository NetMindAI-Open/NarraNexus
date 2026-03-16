/**
 * @file_name: UserInboxPopover.tsx
 * @author: Bin Liang
 * @date: 2025-01-15
 * @description: User Inbox Modal - Centered large dialog for message notifications
 *
 * Features:
 * 1. Bell icon displays unread count badge
 * 2. Click to open centered Dialog with message list on the left + details on the right
 * 3. Detail view has a "Chat with Agent" navigation button
 */

import { useState } from 'react';
import {
  Bell, Mail, MailOpen, RefreshCw, CheckCheck,
  MessageSquare, ExternalLink, Inbox, Clock,
  ChevronLeft, ChevronRight,
} from 'lucide-react';
import { Dialog, DialogContent } from '@/components/ui';
import { Button, Badge, Markdown } from '@/components/ui';
import { useConfigStore, usePreloadStore, useChatStore } from '@/stores';
import { api } from '@/lib/api';
import { cn, formatRelativeTime } from '@/lib/utils';
import type { InboxMessage } from '@/types/api';

/** Build display title with source agent name prefix */
function buildDisplayTitle(message: InboxMessage, agents: { agent_id: string; name?: string }[]): string {
  const raw = message.title || 'No title';
  if (message.source?.type === 'agent' && message.source.id) {
    const agent = agents.find((a) => a.agent_id === message.source!.id);
    if (agent?.name) return `[${agent.name}] ${raw}`;
  }
  return raw;
}

export function UserInboxPopover() {
  const [open, setOpen] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);

  const { userId, setAgentId, agents } = useConfigStore();
  const { setActiveAgent } = useChatStore();
  const {
    inbox: messages,
    inboxUnreadCount: unreadCount,
    inboxTotalCount: totalCount,
    inboxPage: currentPage,
    inboxLoading,
    refreshInbox,
    loadInboxPage,
    updateInboxMessage,
    markAllInboxRead,
  } = usePreloadStore();

  const PAGE_SIZE = 50;
  const totalPages = Math.max(1, Math.ceil(totalCount / PAGE_SIZE));

  const selectedMessage = messages.find((m) => m.message_id === selectedId) ?? null;

  // Refresh messages
  const handleRefresh = async () => {
    setIsRefreshing(true);
    try {
      await refreshInbox(userId);
    } finally {
      setIsRefreshing(false);
    }
  };

  // Mark a single message as read
  const markAsRead = async (messageId: string) => {
    try {
      await api.markMessageRead(messageId);
      updateInboxMessage(messageId, { is_read: true });
    } catch (error) {
      console.error('Failed to mark as read:', error);
    }
  };

  // Mark all messages as read
  const handleMarkAllAsRead = async () => {
    try {
      await api.markAllRead(userId);
      markAllInboxRead();
    } catch (error) {
      console.error('Failed to mark all as read:', error);
    }
  };

  // Click message
  const handleMessageClick = (message: InboxMessage) => {
    setSelectedId(message.message_id);
    if (!message.is_read) {
      markAsRead(message.message_id);
    }
  };

  // Navigate to Agent
  const handleGoToAgent = (targetAgentId: string) => {
    setAgentId(targetAgentId);
    setActiveAgent(targetAgentId);
    setOpen(false);
    setSelectedId(null);
  };

  const handleClose = () => {
    setOpen(false);
    setSelectedId(null);
  };

  return (
    <>
      {/* Bell trigger */}
      <Button variant="ghost" size="icon" className="relative" onClick={() => setOpen(true)}>
        <Bell className="w-5 h-5" />
        {unreadCount > 0 && (
          <span className="absolute -top-1 -right-1 h-5 min-w-5 px-1 flex items-center justify-center text-[10px] font-medium bg-[var(--color-error)] text-white rounded-full shadow-[0_0_8px_rgba(255,77,109,0.5)]">
            {unreadCount > 99 ? '99+' : unreadCount}
          </span>
        )}
      </Button>

      {/* Centered large dialog */}
      <Dialog isOpen={open} onClose={handleClose} size={selectedMessage ? '6xl' : '3xl'} title="Messages">
        <DialogContent className="p-0">
          {/* Toolbar */}
          <div className="flex items-center justify-between px-5 py-3 border-b border-[var(--border-subtle)]">
            <div className="flex items-center gap-3">
              <span className="text-sm text-[var(--text-secondary)]">
                {totalCount} message{totalCount !== 1 ? 's' : ''}
              </span>
              {unreadCount > 0 && (
                <Badge variant="accent" size="sm" glow>
                  {unreadCount} unread
                </Badge>
              )}
            </div>
            <div className="flex items-center gap-1">
              {unreadCount > 0 && (
                <Button variant="ghost" size="sm" onClick={handleMarkAllAsRead} className="text-xs gap-1.5">
                  <CheckCheck className="w-3.5 h-3.5" />
                  Mark all read
                </Button>
              )}
              <Button variant="ghost" size="icon" onClick={handleRefresh} disabled={isRefreshing}>
                <RefreshCw className={cn('w-4 h-4', isRefreshing && 'animate-spin')} />
              </Button>
            </div>
          </div>

          {/* Main body: left list + right details */}
          <div className="flex h-[600px]">
            {/* Left side - Message list */}
            <div className={cn(
              'border-r border-[var(--border-subtle)] flex flex-col',
              selectedMessage ? 'w-[320px] shrink-0' : 'w-full',
            )}>
              {messages.length === 0 && !inboxLoading ? (
                <div className="h-full flex flex-col items-center justify-center text-[var(--text-tertiary)]">
                  <div className="w-16 h-16 rounded-2xl bg-[var(--bg-secondary)] flex items-center justify-center mb-4">
                    <Inbox className="w-8 h-8 opacity-40" />
                  </div>
                  <p className="text-sm font-medium">No messages yet</p>
                  <p className="text-xs mt-1 opacity-60">Messages from agents will appear here</p>
                </div>
              ) : (
                <>
                  <div className="flex-1 overflow-y-auto p-2 space-y-0.5">
                    {messages.map((message) => (
                      <button
                        key={message.message_id}
                        onClick={() => handleMessageClick(message)}
                        className={cn(
                          'w-full text-left p-3 rounded-xl transition-all',
                          'hover:bg-[var(--bg-secondary)]',
                          selectedId === message.message_id && 'bg-[var(--accent-primary)]/10 border border-[var(--accent-primary)]/20',
                          !message.is_read && selectedId !== message.message_id && 'bg-[var(--accent-primary)]/5',
                        )}
                      >
                        <div className="flex items-start gap-2.5">
                          {message.is_read ? (
                            <MailOpen className="w-4 h-4 text-[var(--text-tertiary)] mt-0.5 shrink-0" />
                          ) : (
                            <Mail className="w-4 h-4 text-[var(--accent-primary)] mt-0.5 shrink-0" />
                          )}
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center justify-between gap-2">
                              <span className={cn(
                                'text-sm truncate',
                                !message.is_read ? 'font-semibold text-[var(--text-primary)]' : 'font-medium text-[var(--text-secondary)]',
                              )}>
                                {buildDisplayTitle(message, agents)}
                              </span>
                            </div>
                            {/* In detail mode show only title, in list mode show preview */}
                            {!selectedMessage && (
                              <p className="text-xs text-[var(--text-tertiary)] mt-1 line-clamp-2">
                                {message.content}
                              </p>
                            )}
                            <div className="flex items-center gap-2 mt-1.5">
                              <span className="text-[10px] text-[var(--text-tertiary)] flex items-center gap-1">
                                <Clock className="w-3 h-3" />
                                {message.created_at && formatRelativeTime(message.created_at)}
                              </span>
                              {message.message_type && (
                                <Badge size="sm" variant="default">{message.message_type}</Badge>
                              )}
                            </div>
                          </div>
                        </div>
                      </button>
                    ))}
                  </div>

                  {/* Pagination */}
                  {totalPages > 1 && (
                    <div className="flex items-center justify-between px-4 py-2.5 border-t border-[var(--border-subtle)] shrink-0">
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={currentPage <= 1 || inboxLoading}
                        onClick={() => { loadInboxPage(userId, currentPage - 1); setSelectedId(null); }}
                        className="gap-1 text-xs"
                      >
                        <ChevronLeft className="w-3.5 h-3.5" />
                        Prev
                      </Button>
                      <span className="text-xs text-[var(--text-tertiary)]">
                        {currentPage} / {totalPages}
                      </span>
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={currentPage >= totalPages || inboxLoading}
                        onClick={() => { loadInboxPage(userId, currentPage + 1); setSelectedId(null); }}
                        className="gap-1 text-xs"
                      >
                        Next
                        <ChevronRight className="w-3.5 h-3.5" />
                      </Button>
                    </div>
                  )}
                </>
              )}
            </div>

            {/* Right side - Message details */}
            {selectedMessage && (
              <div className="flex-1 overflow-y-auto">
                <div className="p-6 space-y-5">
                  {/* Title area */}
                  <div>
                    <h3 className="text-lg font-bold text-[var(--text-primary)]">
                      {buildDisplayTitle(selectedMessage, agents)}
                    </h3>
                    <div className="flex items-center gap-3 mt-2">
                      {selectedMessage.message_type && (
                        <Badge size="sm" variant="default">{selectedMessage.message_type}</Badge>
                      )}
                      <span className="text-xs text-[var(--text-tertiary)] flex items-center gap-1">
                        <Clock className="w-3 h-3" />
                        {selectedMessage.created_at && formatRelativeTime(selectedMessage.created_at)}
                      </span>
                      {selectedMessage.is_read ? (
                        <span className="text-xs text-[var(--text-tertiary)]">Read</span>
                      ) : (
                        <Badge size="sm" variant="accent">Unread</Badge>
                      )}
                    </div>
                  </div>

                  {/* Content */}
                  <div className="prose-sm text-[var(--text-secondary)] leading-relaxed">
                    <Markdown content={selectedMessage.content} />
                  </div>

                  {/* Source info + actions */}
                  {selectedMessage.source && (
                    <div className="pt-4 border-t border-[var(--border-subtle)] space-y-3">
                      <div className="flex items-center gap-2 text-xs text-[var(--text-tertiary)]">
                        <span className="font-medium">From:</span>
                        <Badge size="sm" variant="default">
                          {selectedMessage.source.type}
                        </Badge>
                        <span className="font-mono">{selectedMessage.source.id}</span>
                      </div>

                      {selectedMessage.source.type === 'agent' && (
                        <Button
                          variant="accent"
                          size="sm"
                          onClick={() => handleGoToAgent(selectedMessage.source!.id!)}
                          className="gap-2"
                          glow
                        >
                          <MessageSquare className="w-4 h-4" />
                          Chat with this Agent
                          <ExternalLink className="w-3 h-3" />
                        </Button>
                      )}
                    </div>
                  )}

                  {/* Related event */}
                  {selectedMessage.event_id && (
                    <div className="pt-4 border-t border-[var(--border-subtle)]">
                      <div className="text-xs text-[var(--text-tertiary)]">
                        <span className="font-medium">Related Event:</span>{' '}
                        <span className="font-mono bg-[var(--bg-secondary)] px-2 py-0.5 rounded">
                          {selectedMessage.event_id}
                        </span>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Right side - Empty state when nothing selected */}
            {!selectedMessage && messages.length > 0 && (
              <div className="hidden" />
            )}
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
