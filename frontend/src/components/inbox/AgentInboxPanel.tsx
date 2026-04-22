/**
 * @file_name: AgentInboxPanel.tsx
 * @author: Bin Liang
 * @date: 2026-03-11
 * @description: Agent Inbox Panel - Displays MessageBus channel messages grouped by room
 * Shows room list with members and expandable messages
 */

import { useState, useMemo } from 'react';
import {
  MailOpen, RefreshCw, Inbox, ChevronRight, ChevronDown,
  Sparkles, Users, Hash,
} from 'lucide-react';
import { Card, CardHeader, CardTitle, CardContent, Button, Badge, Markdown, StatStrip } from '@/components/ui';
import { useConfigStore, usePreloadStore } from '@/stores';
import { cn, formatRelativeTime } from '@/lib/utils';
import { api } from '@/lib/api';
import type { InboxRoom } from '@/types/api';

// Local KPI card was removed — this panel now uses the shared <StatStrip />.

export function AgentInboxPanel() {
  const [expandedRoomId, setExpandedRoomId] = useState<string | null>(null);
  const [loadedAll, setLoadedAll] = useState(false);

  const { agentId } = useConfigStore();
  const {
    agentInboxRooms: rooms,
    agentInboxUnreadCount: unreadCount,
    agentInboxLoading: loading,
    refreshAgentInbox,
  } = usePreloadStore();

  const handleRefresh = () => {
    setLoadedAll(false);
    // Pass limit=0 to reset stored _inboxLimit back to default (50)
    refreshAgentInbox(agentId, false, 0);
  };

  const handleLoadAll = () => {
    setLoadedAll(true);
    refreshAgentInbox(agentId, false, -1);
  };

  const toggleRoom = (roomId: string) => {
    const nextId = expandedRoomId === roomId ? null : roomId;
    setExpandedRoomId(nextId);

    // When opening a room with unread messages, advance the read cursor to
    // the latest message. Backend updates last_read_at; we refresh inbox
    // afterward so badges disappear without requiring a manual reload.
    if (nextId) {
      const room: InboxRoom | undefined = rooms.find((r) => r.room_id === nextId);
      if (room && room.unread_count > 0 && room.messages.length > 0 && agentId) {
        const latest = [...room.messages].sort((a, b) => {
          const ta = a.created_at ? new Date(a.created_at).getTime() : 0;
          const tb = b.created_at ? new Date(b.created_at).getTime() : 0;
          return tb - ta;
        })[0];
        if (latest?.message_id) {
          api.markAgentMessageRead(latest.message_id, agentId)
            .then(() => refreshAgentInbox(agentId, true))
            .catch(() => { /* non-fatal: badge will refresh on next poll */ });
        }
      }
    }
  };

  // Calculate metrics
  const metrics = useMemo(() => {
    const totalMessages = rooms.reduce((sum, r) => sum + r.messages.length, 0);
    const readCount = totalMessages - unreadCount;
    const readRate = totalMessages > 0 ? Math.round((readCount / totalMessages) * 100) : 0;
    return { totalMessages, readRate };
  }, [rooms, unreadCount]);

  // Sort rooms by latest activity (newest first), and sort each room's
  // messages by created_at descending (newest first).
  const sortedRooms = useMemo(() => {
    const toTime = (s?: string | null) => (s ? new Date(s).getTime() : 0);
    return rooms
      .map((room) => ({
        ...room,
        messages: [...room.messages].sort(
          (a, b) => toTime(b.created_at) - toTime(a.created_at)
        ),
      }))
      .sort((a, b) => toTime(b.latest_at) - toTime(a.latest_at));
  }, [rooms]);

  return (
    <Card className="flex flex-col h-full">
      <CardHeader>
        <CardTitle>
          <Inbox />
          Agent Inbox
          {unreadCount > 0 && (
            <span className="ml-1 text-[var(--color-yellow-500)] tabular-nums normal-case tracking-normal">
              · {unreadCount}
            </span>
          )}
        </CardTitle>
        <div className="flex items-center gap-1">
          {!loadedAll && rooms.length > 0 && (
            <Button
              variant="ghost"
              size="sm"
              onClick={handleLoadAll}
              disabled={loading}
              title="Load all messages"
            >
              Load all
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

      {rooms.length > 0 && (
        <StatStrip
          items={[
            { label: 'Unread', value: unreadCount, icon: Sparkles, tone: 'warning', pulse: unreadCount > 0, subtext: 'New' },
            { label: 'Rooms', value: rooms.length, icon: Hash, tone: 'secondary', subtext: 'Channels' },
            { label: 'Read', value: `${metrics.readRate}%`, icon: MailOpen, tone: 'success', subtext: 'Rate' },
          ]}
        />
      )}

      <CardContent className="flex-1 overflow-y-auto space-y-2 min-h-0 py-2">
        {rooms.length === 0 ? (
          <div className="h-full flex items-center justify-center">
            <div className="text-center p-8">
              <div className="w-14 h-14 rounded-2xl bg-[var(--accent-primary)]/10 mx-auto mb-4 flex items-center justify-center">
                <Inbox className="w-7 h-7 text-[var(--accent-primary)]" />
              </div>
              <p className="text-[var(--text-secondary)] text-sm font-medium mb-1">No messages</p>
              <p className="text-[var(--text-tertiary)] text-xs">Channel messages will appear here</p>
            </div>
          </div>
        ) : (
          sortedRooms.map((room) => {
            const isRoomExpanded = expandedRoomId === room.room_id;

            return (
              <div
                key={room.room_id}
                className={cn(
                  'rounded-xl border transition-all duration-300',
                  isRoomExpanded
                    ? 'border-[var(--accent-primary)]/30 shadow-[0_0_20px_var(--accent-glow)]'
                    : 'border-[var(--border-subtle)] hover:border-[var(--accent-primary)]/20',
                  room.unread_count > 0 && !isRoomExpanded && 'bg-[var(--accent-glow)]'
                )}
              >
                {/* Room Header */}
                <button
                  onClick={() => toggleRoom(room.room_id)}
                  className="w-full text-left p-3 flex items-center gap-3"
                >
                  <div className={cn(
                    'w-8 h-8 rounded-lg flex items-center justify-center shrink-0',
                    room.unread_count > 0
                      ? 'bg-[var(--accent-glow)] shadow-[0_0_10px_var(--accent-glow)]'
                      : 'bg-[var(--bg-tertiary)]'
                  )}>
                    <Hash className={cn(
                      'w-4 h-4',
                      room.unread_count > 0 ? 'text-[var(--accent-primary)]' : 'text-[var(--text-tertiary)]'
                    )} />
                  </div>

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

                  <div className="flex items-center gap-2 shrink-0">
                    {room.latest_at && (
                      <span className="text-[9px] text-[var(--text-tertiary)] font-mono">
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

                {/* Room Content (Members + Messages) */}
                {isRoomExpanded && (
                  <div className="px-3 pb-3 space-y-2">
                    {/* Members */}
                    <div className="flex flex-wrap gap-1.5 px-1 pb-2 border-b border-[var(--border-subtle)]">
                      {room.members.map((member) => (
                        <div
                          key={member.agent_id}
                          className="flex items-center gap-1 px-2 py-1 rounded-md bg-[var(--bg-tertiary)] text-[10px]"
                        >
                          <span className="font-medium text-[var(--text-secondary)]">{member.agent_name}</span>
                          <span className="text-[var(--text-tertiary)]">{member.agent_id}</span>
                        </div>
                      ))}
                    </div>

                    {/* Messages (chat-style list) */}
                    <div className="space-y-1">
                      {room.messages.map((msg) => (
                        <div key={msg.message_id} className="px-1 py-1">
                          <div className="flex items-baseline gap-2">
                            <span className="text-xs font-medium text-[var(--accent-primary)] shrink-0">
                              {msg.sender_name}
                            </span>
                            <span className="text-[9px] text-[var(--text-tertiary)] font-mono shrink-0">
                              {msg.created_at && formatRelativeTime(msg.created_at)}
                            </span>
                          </div>
                          <div className="text-xs text-[var(--text-secondary)] mt-0.5 leading-relaxed [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
                            <Markdown content={msg.content} />
                          </div>
                        </div>
                      ))}
                    </div>
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

export default AgentInboxPanel;
