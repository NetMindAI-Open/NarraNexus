/**
 * Inbox Panel - Messages from agents (User Inbox) and to agents (Agent Inbox)
 * Uses preloaded data from preloadStore for instant tab switching
 */

import { useState } from 'react';
import { Mail, MailOpen, RefreshCw, CheckCheck, User, Bot, Hash, Users, ChevronRight, ChevronDown, ChevronLeft } from 'lucide-react';
import { Card, CardHeader, CardTitle, CardContent, Button, Badge, Markdown } from '@/components/ui';
import { useConfigStore, usePreloadStore } from '@/stores';
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

type InboxTab = 'user' | 'agent';

export function InboxPanel() {
  const [activeTab, setActiveTab] = useState<InboxTab>('user');
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [expandedRoomId, setExpandedRoomId] = useState<string | null>(null);

  const { userId, agentId, agents } = useConfigStore();
  const {
    // User Inbox
    inbox: userMessages,
    inboxUnreadCount: userUnreadCount,
    inboxTotalCount: userTotalCount,
    inboxPage: userPage,
    inboxLoading: userLoading,
    refreshInbox,
    loadInboxPage,
    updateInboxMessage,
    markAllInboxRead,
    // Agent Inbox
    agentInboxRooms: agentRooms,
    agentInboxUnreadCount: agentUnreadCount,
    agentInboxLoading: agentLoading,
    refreshAgentInbox,
  } = usePreloadStore();

  const PAGE_SIZE = 50;
  const userTotalPages = Math.max(1, Math.ceil(userTotalCount / PAGE_SIZE));

  const handleRefresh = () => {
    if (activeTab === 'user') {
      refreshInbox(userId);
    } else {
      refreshAgentInbox(agentId);
    }
  };

  const markAsRead = async (messageId: string) => {
    try {
      await api.markMessageRead(messageId);
      updateInboxMessage(messageId, { is_read: true });
    } catch (error) {
      console.error('Failed to mark as read:', error);
    }
  };

  const handleMarkAllAsRead = async () => {
    try {
      await api.markAllRead(userId);
      markAllInboxRead();
    } catch (error) {
      console.error('Failed to mark all as read:', error);
    }
  };

  const toggleExpand = (messageId: string) => {
    if (expandedId === messageId) {
      setExpandedId(null);
    } else {
      setExpandedId(messageId);
      // Mark as read when expanded (only for user inbox)
      if (activeTab === 'user') {
        const msg = userMessages.find((m) => m.message_id === messageId);
        if (msg && !msg.is_read) {
          markAsRead(messageId);
        }
      }
    }
  };

  const toggleRoom = (roomId: string) => {
    if (expandedRoomId === roomId) {
      setExpandedRoomId(null);
      setExpandedId(null);
    } else {
      setExpandedRoomId(roomId);
      setExpandedId(null);
    }
  };


  const loading = activeTab === 'user' ? userLoading : agentLoading;

  return (
    <Card className="flex flex-col h-full">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Mail className="w-4 h-4 text-[var(--color-accent)]" />
          Inbox
        </CardTitle>
        <div className="flex items-center gap-2">
          {activeTab === 'user' && userUnreadCount > 0 && (
            <Badge variant="accent" pulse>
              {userUnreadCount}
            </Badge>
          )}
          {activeTab === 'agent' && agentUnreadCount > 0 && (
            <Badge variant="accent" pulse>
              {agentUnreadCount}
            </Badge>
          )}
          {activeTab === 'user' && (
            <Button variant="ghost" size="icon" onClick={handleMarkAllAsRead} title="Mark all read">
              <CheckCheck className="w-4 h-4" />
            </Button>
          )}
          <Button
            variant="ghost"
            size="icon"
            onClick={handleRefresh}
            disabled={loading}
            title="Refresh"
          >
            <RefreshCw className={cn('w-4 h-4', loading && 'animate-spin')} />
          </Button>
        </div>
      </CardHeader>

      {/* Tab Switcher */}
      <div className="px-4 pb-2">
        <div className="flex gap-1 p-1 bg-[var(--bg-tertiary)] rounded-lg">
          <button
            onClick={() => {
              setActiveTab('user');
              setExpandedId(null);
              setExpandedRoomId(null);
            }}
            className={cn(
              'flex-1 flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all',
              activeTab === 'user'
                ? 'bg-[var(--bg-primary)] text-[var(--text-primary)] shadow-sm'
                : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
            )}
          >
            <User className="w-3 h-3" />
            User Inbox
            {userUnreadCount > 0 && (
              <span className="ml-1 px-1.5 py-0.5 text-[10px] bg-[var(--color-accent)] text-white rounded-full">
                {userUnreadCount}
              </span>
            )}
          </button>
          <button
            onClick={() => {
              setActiveTab('agent');
              setExpandedId(null);
              setExpandedRoomId(null);
            }}
            className={cn(
              'flex-1 flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all',
              activeTab === 'agent'
                ? 'bg-[var(--bg-primary)] text-[var(--text-primary)] shadow-sm'
                : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
            )}
          >
            <Bot className="w-3 h-3" />
            Agent Inbox
            {agentUnreadCount > 0 && (
              <span className="ml-1 px-1.5 py-0.5 text-[10px] bg-[var(--color-accent)] text-white rounded-full">
                {agentUnreadCount}
              </span>
            )}
          </button>
        </div>
      </div>

      <CardContent className="flex-1 overflow-y-auto space-y-2 min-h-0">
        {/* User Inbox */}
        {activeTab === 'user' && (
          <>
            {userMessages.length === 0 ? (
              <div className="h-full flex items-center justify-center">
                <p className="text-[var(--text-tertiary)] text-sm">No messages</p>
              </div>
            ) : (
              <>
                {userMessages.map((message) => (
                  <button
                    key={message.message_id}
                    onClick={() => toggleExpand(message.message_id)}
                    className={cn(
                      'w-full text-left p-3 rounded-lg transition-all',
                      'border border-[var(--border-default)]',
                      'hover:border-[var(--color-accent)]/50',
                      !message.is_read && 'bg-[var(--accent-10)] border-[var(--color-accent)]/30',
                      expandedId === message.message_id && 'ring-1 ring-[var(--color-accent)]'
                    )}
                  >
                    <div className="flex items-start gap-2">
                      {message.is_read ? (
                        <MailOpen className="w-4 h-4 text-[var(--text-tertiary)] mt-0.5" />
                      ) : (
                        <Mail className="w-4 h-4 text-[var(--color-accent)] mt-0.5" />
                      )}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center justify-between gap-2">
                          <span
                            className={cn(
                              'text-sm font-medium truncate',
                              !message.is_read ? 'text-[var(--text-primary)]' : 'text-[var(--text-secondary)]'
                            )}
                          >
                            {buildDisplayTitle(message, agents)}
                          </span>
                          <div className="flex items-center gap-1 shrink-0">
                            {message.message_type && (
                              <Badge size="sm" variant="default">
                                {message.message_type}
                              </Badge>
                            )}
                            <span className="text-[10px] text-[var(--text-tertiary)] font-mono">
                              {message.created_at && formatRelativeTime(message.created_at)}
                            </span>
                          </div>
                        </div>
                        <div
                          className={cn(
                            'text-xs mt-1',
                            expandedId === message.message_id
                              ? 'text-[var(--text-secondary)] max-h-[400px] overflow-y-auto'
                              : 'text-[var(--text-tertiary)] line-clamp-2'
                          )}
                        >
                          {expandedId === message.message_id ? (
                            <Markdown content={message.content} />
                          ) : (
                            <span className="whitespace-pre-wrap">{message.content}</span>
                          )}
                        </div>
                      </div>
                    </div>
                  </button>
                ))}

                {/* Pagination */}
                {userTotalPages > 1 && (
                  <div className="flex items-center justify-between pt-2">
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={userPage <= 1 || userLoading}
                      onClick={() => { loadInboxPage(userId, userPage - 1); setExpandedId(null); }}
                      className="gap-1 text-xs"
                    >
                      <ChevronLeft className="w-3.5 h-3.5" />
                      Prev
                    </Button>
                    <span className="text-xs text-[var(--text-tertiary)]">
                      {userPage} / {userTotalPages}
                    </span>
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={userPage >= userTotalPages || userLoading}
                      onClick={() => { loadInboxPage(userId, userPage + 1); setExpandedId(null); }}
                      className="gap-1 text-xs"
                    >
                      Next
                      <ChevronRight className="w-3.5 h-3.5" />
                    </Button>
                  </div>
                )}
              </>
            )}
          </>
        )}

        {/* Agent Inbox (Room-based) */}
        {activeTab === 'agent' && (
          <>
            {agentRooms.length === 0 ? (
              <div className="h-full flex items-center justify-center">
                <p className="text-[var(--text-tertiary)] text-sm">No messages</p>
              </div>
            ) : (
              agentRooms.map((room) => {
                const isRoomExpanded = expandedRoomId === room.room_id;

                return (
                  <div
                    key={room.room_id}
                    className={cn(
                      'rounded-lg border transition-all',
                      isRoomExpanded
                        ? 'border-[var(--color-accent)]/30 ring-1 ring-[var(--color-accent)]'
                        : 'border-[var(--border-default)] hover:border-[var(--color-accent)]/50',
                      room.unread_count > 0 && !isRoomExpanded && 'bg-[var(--accent-10)]'
                    )}
                  >
                    {/* Room Header */}
                    <button
                      onClick={() => toggleRoom(room.room_id)}
                      className="w-full text-left p-3 flex items-center gap-2"
                    >
                      <Hash className={cn(
                        'w-4 h-4 shrink-0',
                        room.unread_count > 0 ? 'text-[var(--color-accent)]' : 'text-[var(--text-tertiary)]'
                      )} />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium text-[var(--text-primary)] truncate">
                            {room.room_name || 'Unnamed Room'}
                          </span>
                          {room.unread_count > 0 && (
                            <Badge size="sm" variant="accent" pulse>
                              {room.unread_count}
                            </Badge>
                          )}
                        </div>
                        <div className="flex items-center gap-1 mt-0.5">
                          <Users className="w-3 h-3 text-[var(--text-tertiary)]" />
                          <span className="text-[10px] text-[var(--text-tertiary)] truncate">
                            {room.members.map((m) => m.agent_name).join(', ')}
                          </span>
                        </div>
                      </div>
                      <div className="flex items-center gap-1 shrink-0">
                        {room.latest_at && (
                          <span className="text-[10px] text-[var(--text-tertiary)] font-mono">
                            {formatRelativeTime(room.latest_at)}
                          </span>
                        )}
                        {isRoomExpanded ? (
                          <ChevronDown className="w-4 h-4 text-[var(--text-tertiary)]" />
                        ) : (
                          <ChevronRight className="w-4 h-4 text-[var(--text-tertiary)]" />
                        )}
                      </div>
                    </button>

                    {/* Room Messages */}
                    {isRoomExpanded && (
                      <div className="px-3 pb-3 space-y-1.5">
                        {/* Members Bar */}
                        <div className="flex flex-wrap gap-1 px-1 pb-2 border-b border-[var(--border-default)]">
                          {room.members.map((member) => (
                            <span
                              key={member.matrix_user_id}
                              className="px-1.5 py-0.5 rounded bg-[var(--bg-tertiary)] text-[10px] text-[var(--text-tertiary)]"
                            >
                              {member.agent_name} <span className="opacity-60">{member.matrix_user_id}</span>
                            </span>
                          ))}
                        </div>

                        {/* Messages (chat-style) */}
                        {room.messages.map((msg) => (
                          <div key={msg.message_id} className="px-1 py-1">
                            <div className="flex items-baseline gap-2">
                              <span className="text-xs font-medium text-[var(--color-accent)] shrink-0">
                                {msg.sender_name}
                              </span>
                              <span className="text-[10px] text-[var(--text-tertiary)] font-mono shrink-0">
                                {msg.created_at && formatRelativeTime(msg.created_at)}
                              </span>
                            </div>
                            <div className="text-xs text-[var(--text-secondary)] mt-0.5 leading-relaxed [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
                              <Markdown content={msg.content} />
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
