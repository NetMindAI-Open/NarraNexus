/**
 * Inbox Panel - Agent Inbox (IM channel messages grouped by room)
 * Uses preloaded data from preloadStore for instant tab switching
 */

import { useState } from 'react';
import { Mail, RefreshCw, Hash, Users, ChevronRight, ChevronDown } from 'lucide-react';
import { Card, CardHeader, CardTitle, CardContent, Button, Badge, Markdown } from '@/components/ui';
import { useConfigStore, usePreloadStore } from '@/stores';
import { cn, formatRelativeTime } from '@/lib/utils';
import { api } from '@/lib/api';

export function InboxPanel() {
  const [expandedRoomId, setExpandedRoomId] = useState<string | null>(null);

  const { agentId } = useConfigStore();
  const {
    agentInboxRooms: agentRooms,
    agentInboxUnreadCount: agentUnreadCount,
    agentInboxLoading: loading,
    refreshAgentInbox,
  } = usePreloadStore();

  const handleRefresh = () => refreshAgentInbox(agentId);

  const toggleRoom = (roomId: string) => {
    const nextId = expandedRoomId === roomId ? null : roomId;
    setExpandedRoomId(nextId);

    if (nextId) {
      const room = agentRooms.find((r) => r.room_id === nextId);
      if (room && room.unread_count > 0 && room.messages.length > 0 && agentId) {
        const latest = [...room.messages].sort((a, b) => {
          const ta = a.created_at ? new Date(a.created_at).getTime() : 0;
          const tb = b.created_at ? new Date(b.created_at).getTime() : 0;
          return tb - ta;
        })[0];
        if (latest?.message_id) {
          api.markAgentMessageRead(latest.message_id, agentId)
            .then(() => refreshAgentInbox(agentId, true))
            .catch(() => { /* non-fatal */ });
        }
      }
    }
  };

  return (
    <Card className="flex flex-col h-full">
      <CardHeader>
        <CardTitle>
          <Mail />
          Agent Inbox
          {agentUnreadCount > 0 && (
            <span className="ml-1 text-[var(--color-yellow-500)] tabular-nums normal-case tracking-normal">
              · {agentUnreadCount}
            </span>
          )}
        </CardTitle>
        <Button
          variant="ghost"
          size="icon"
          onClick={handleRefresh}
          disabled={loading}
          title="Refresh"
        >
          <RefreshCw className={cn('w-4 h-4', loading && 'animate-spin')} />
        </Button>
      </CardHeader>

      <CardContent className="flex-1 overflow-y-auto min-h-0 !p-0">
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
                  'border-b border-[var(--rule)] transition-colors',
                  room.unread_count > 0 && !isRoomExpanded && 'bg-[var(--bg-secondary)]'
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
                          key={member.agent_id}
                          className="px-1.5 py-0.5 rounded bg-[var(--bg-tertiary)] text-[10px] text-[var(--text-tertiary)]"
                        >
                          {member.agent_name} <span className="opacity-60">{member.agent_id}</span>
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
      </CardContent>
    </Card>
  );
}
