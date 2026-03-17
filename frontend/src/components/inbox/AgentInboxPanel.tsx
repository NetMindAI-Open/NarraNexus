/**
 * @file_name: AgentInboxPanel.tsx
 * @author: Bin Liang
 * @date: 2026-03-11
 * @description: Agent Inbox Panel - Displays Matrix channel messages grouped by room
 * Shows room list with members (agent_name, matrix_user_id) and expandable messages
 */

import { useState, useMemo } from 'react';
import {
  MailOpen, RefreshCw, Inbox, ChevronRight, ChevronDown,
  Sparkles, Users, Hash,
} from 'lucide-react';
import { Card, CardHeader, CardTitle, CardContent, Button, Badge, Markdown } from '@/components/ui';
import { useConfigStore, usePreloadStore } from '@/stores';
import { cn, formatRelativeTime } from '@/lib/utils';

// KPI Card Component
function KPICard({
  label,
  value,
  icon: Icon,
  color = 'accent',
  subtext,
  pulse,
}: {
  label: string;
  value: string | number;
  icon: React.ElementType;
  color?: 'accent' | 'success' | 'warning' | 'secondary';
  subtext?: string;
  pulse?: boolean;
}) {
  const colorMap = {
    accent: {
      bg: 'bg-[var(--accent-glow)]',
      icon: 'text-[var(--accent-primary)]',
      value: 'text-[var(--accent-primary)]',
      glow: 'shadow-[0_0_15px_var(--accent-glow)]',
    },
    success: {
      bg: 'bg-[var(--color-success)]/10',
      icon: 'text-[var(--color-success)]',
      value: 'text-[var(--color-success)]',
      glow: 'shadow-[0_0_15px_rgba(34,197,94,0.2)]',
    },
    warning: {
      bg: 'bg-[var(--color-warning)]/10',
      icon: 'text-[var(--color-warning)]',
      value: 'text-[var(--color-warning)]',
      glow: 'shadow-[0_0_15px_rgba(234,179,8,0.2)]',
    },
    secondary: {
      bg: 'bg-[var(--accent-secondary)]/10',
      icon: 'text-[var(--accent-secondary)]',
      value: 'text-[var(--accent-secondary)]',
      glow: 'shadow-[0_0_15px_rgba(192,132,252,0.2)]',
    },
  };

  const colors = colorMap[color];

  return (
    <div
      className={cn(
        'p-2.5 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)]',
        'transition-all duration-300 hover:border-[var(--accent-primary)]/30',
        pulse && colors.glow
      )}
    >
      <div className="flex items-center gap-2 mb-1.5">
        <div className={cn('w-6 h-6 rounded-lg flex items-center justify-center', colors.bg)}>
          <Icon className={cn('w-3 h-3', colors.icon, pulse && 'animate-pulse')} />
        </div>
        <span className="text-[9px] text-[var(--text-tertiary)] uppercase tracking-wider font-medium">{label}</span>
      </div>
      <div className={cn('text-lg font-bold font-mono', colors.value)}>{value}</div>
      {subtext && <div className="text-[8px] text-[var(--text-tertiary)] mt-0.5 font-mono truncate">{subtext}</div>}
    </div>
  );
}

export function AgentInboxPanel() {
  const [expandedRoomId, setExpandedRoomId] = useState<string | null>(null);

  const { agentId } = useConfigStore();
  const {
    agentInboxRooms: rooms,
    agentInboxUnreadCount: unreadCount,
    agentInboxLoading: loading,
    refreshAgentInbox,
  } = usePreloadStore();

  const handleRefresh = () => {
    refreshAgentInbox(agentId);
  };

  const toggleRoom = (roomId: string) => {
    setExpandedRoomId(expandedRoomId === roomId ? null : roomId);
  };

  // Calculate metrics
  const metrics = useMemo(() => {
    const totalMessages = rooms.reduce((sum, r) => sum + r.messages.length, 0);
    const readCount = totalMessages - unreadCount;
    const readRate = totalMessages > 0 ? Math.round((readCount / totalMessages) * 100) : 0;
    return { totalMessages, readRate };
  }, [rooms, unreadCount]);

  return (
    <Card variant="glass" className="flex flex-col h-full">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg bg-[var(--accent-primary)]/10 flex items-center justify-center">
            <Inbox className="w-4 h-4 text-[var(--accent-primary)]" />
          </div>
          <span>Agent Inbox</span>
        </CardTitle>
        <div className="flex items-center gap-2">
          {unreadCount > 0 && (
            <Badge variant="accent" pulse glow className="font-mono">
              {unreadCount}
            </Badge>
          )}
          <Button
            variant="ghost"
            size="icon"
            onClick={handleRefresh}
            disabled={loading}
            title="Refresh"
            className="hover:bg-[var(--accent-glow)]"
          >
            <RefreshCw className={cn('w-4 h-4', loading && 'animate-spin')} />
          </Button>
        </div>
      </CardHeader>

      {/* Dashboard KPI Section */}
      {rooms.length > 0 && (
        <div className="px-4 pb-3 space-y-3">
          <div className="grid grid-cols-3 gap-2">
            <KPICard
              label="Unread"
              value={unreadCount}
              icon={Sparkles}
              color="accent"
              pulse={unreadCount > 0}
              subtext="New messages"
            />
            <KPICard
              label="Rooms"
              value={rooms.length}
              icon={Hash}
              color="secondary"
              subtext="Matrix rooms"
            />
            <KPICard
              label="Read"
              value={`${metrics.readRate}%`}
              icon={MailOpen}
              color="success"
              subtext="Rate"
            />
          </div>
        </div>
      )}

      <CardContent className="flex-1 overflow-y-auto space-y-2 min-h-0 py-2">
        {rooms.length === 0 ? (
          <div className="h-full flex items-center justify-center">
            <div className="text-center p-8">
              <div className="w-14 h-14 rounded-2xl bg-[var(--accent-primary)]/10 mx-auto mb-4 flex items-center justify-center">
                <Inbox className="w-7 h-7 text-[var(--accent-primary)]" />
              </div>
              <p className="text-[var(--text-secondary)] text-sm font-medium mb-1">No messages</p>
              <p className="text-[var(--text-tertiary)] text-xs">Matrix channel messages will appear here</p>
            </div>
          </div>
        ) : (
          rooms.map((room) => {
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
                          key={member.matrix_user_id}
                          className="flex items-center gap-1 px-2 py-1 rounded-md bg-[var(--bg-tertiary)] text-[10px]"
                        >
                          <span className="font-medium text-[var(--text-secondary)]">{member.agent_name}</span>
                          <span className="text-[var(--text-tertiary)]">{member.matrix_user_id}</span>
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
