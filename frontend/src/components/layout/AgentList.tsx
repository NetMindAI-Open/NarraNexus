/**
 * AgentList - Agent selection, creation, editing, and management
 * Shows running indicators and completion badges for multi-agent concurrent chat.
 */

import { useState, useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import {
  RefreshCw,
  Plus,
  Pencil,
  Check,
  X,
  Globe,
  Lock,
  Trash2,
  Loader2,
  ListChecks,
} from 'lucide-react';
import { Button, useConfirm } from '@/components/ui';
import { RingAvatar, BracketSectionLabel, BracketEmptyState } from '@/components/nm';
import { useConfigStore, useChatStore } from '@/stores';
import { useCreateAgent } from '@/hooks';
import { api } from '@/lib/api';
import { cn, formatChatTimestamp } from '@/lib/utils';
import { getLastReadMs, markAgentRead, countUnread, latestMessageMs } from '@/lib/unread';

interface AgentListProps {
  collapsed: boolean;
  /** When non-null, only render agents whose IDs are in this list (used by TeamFilterBar). */
  filterAgentIds?: string[] | null;
}

/**
 * Feature flag — temporary, 2026-05-18.
 * Hides the "set agent public/private" toggle button in the sidebar
 * while the public-agent feature is paused for product redesign.
 *
 * Scope of the flag:
 *  - HIDES: the per-agent Globe/Lock toggle button (set-public entry point)
 *  - KEEPS: the read-only Globe badge that marks other users' already-public
 *           agents, and the dashboard's PublicCard for displaying them —
 *           these aren't user-actionable controls, they're status indicators
 *           for legacy `is_public=1` rows that may still exist.
 *  - KEEPS: the backend endpoint and the `handleTogglePublic` function so
 *           re-enabling is a one-line flip when the feature ships again.
 *
 * Flip this to `true` to bring the toggle back. */
const SHOW_AGENT_PUBLIC_TOGGLE = false;

export function AgentList({ collapsed, filterAgentIds }: AgentListProps) {
  const [loadingAgents, setLoadingAgents] = useState(false);
  const { createAgent, creating: creatingAgent } = useCreateAgent();
  const [editingAgentId, setEditingAgentId] = useState<string | null>(null);
  const [editingName, setEditingName] = useState('');
  const [savingName, setSavingName] = useState(false);
  const [deletingAgentId, setDeletingAgentId] = useState<string | null>(null);
  // Note: team management lives behind the gear icon on TeamFilterBar.
  // The button next to "+" here is for batch agent CRUD (manage agents page),
  // not team membership — the two are conceptually distinct.

  const navigate = useNavigate();
  const location = useLocation();
  const { userId, agentId, agents: rawAgents, setAgentId, setAgents, refreshAgents } = useConfigStore();
  const agents = filterAgentIds == null
    ? rawAgents
    : rawAgents.filter((a) => filterAgentIds.includes(a.agent_id));
  const { setActiveAgent, clearAgent, isAgentStreaming, completedAgentIds } = useChatStore();
  // Live read of every agent's session so the row's last-message preview /
  // time / unread count update as new messages stream in. Reading the
  // sessions map (not per-agent slice) is the simplest path; zustand
  // re-renders this component when ANY session updates, which is fine for
  // a list of agents that's already small.
  const agentSessions = useChatStore((s) => s.agentSessions);
  const { confirm, alert, dialog: confirmDialog } = useConfirm();

  // Mark the active agent's messages as read so its unread count stays
  // cleared after the user navigates away. Without this the count only
  // zeroed while the row was active and reappeared on switch-away (the
  // marker was never advanced by reading). Re-runs when the active agent
  // changes or its messages grow (e.g. a reply settles while viewing).
  useEffect(() => {
    if (!agentId) return;
    const latest = latestMessageMs(agentSessions[agentId]?.messages ?? []);
    if (latest > 0) markAgentRead(agentId, latest);
  }, [agentId, agentSessions]);

  /**
   * Derive the per-agent meta shown in each row: agent-reply preview +
   * activity time and an unread count.
   *
   * Preview is the most recent **assistant** message — NM messenger UX:
   * each row "belongs" to the agent, so the second line previews what
   * the agent last said to the user, not what the user just typed.
   *
   * Data source priority (latest-wins by timestamp):
   *   1. Local session — if the live stream just produced a reply that
   *      has not yet been re-fetched from /agents, prefer it so the
   *      sidebar updates in real-time without polling.
   *   2. Server-supplied last_assistant_preview from /agents, which
   *      covers the common case (other agents the user hasn't opened
   *      yet in this session — their `messages` array is empty).
   *   3. Empty (renders the agent description placeholder).
   *
   * Unread count = messages whose timestamp is strictly newer than the
   * localStorage `lastSeenAwarenessTime:<agent>` marker AND not authored
   * by the local user. If the agent is the currently selected one we
   * treat it as "all seen" so the count zeroes out.
   */
  const getRowMeta = (aid: string) => {
    const session = agentSessions[aid];
    const messages = session?.messages ?? [];
    let sessionLast: typeof messages[number] | null = null;
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'assistant' && messages[i].content) {
        sessionLast = messages[i];
        break;
      }
    }
    const agent = rawAgents.find((a) => a.agent_id === aid);
    const serverPreview = agent?.last_assistant_preview || '';
    const serverAtMs = agent?.last_assistant_at
      ? new Date(agent.last_assistant_at).getTime()
      : 0;

    const sessionAtMs = sessionLast?.timestamp ?? 0;
    let preview = '';
    let timeMs = 0;
    if (sessionLast && sessionAtMs >= serverAtMs) {
      preview = sessionLast.content.replace(/\s+/g, ' ').slice(0, 60);
      timeMs = sessionAtMs;
    } else if (serverPreview) {
      preview = serverPreview.replace(/\s+/g, ' ').slice(0, 60);
      timeMs = serverAtMs;
    }
    // IM-sidebar formatter: today→HH:MM, yesterday→Yesterday, within week→
    // weekday (Wed), older same year→May 18, cross-year→YYYY/MM/DD. Plain
    // HH:MM:SS hid the date so messages from days ago looked like "this
    // morning". 2026-05-27.
    const time = timeMs ? formatChatTimestamp(timeMs) : '';
    // Unread = agent messages newer than the per-agent read marker. The
    // active row is always treated as read (its marker is advanced by the
    // effect below). See lib/unread for why this has its own marker rather
    // than reusing the Awareness-tab one.
    const unread = aid !== agentId ? countUnread(messages, getLastReadMs(aid)) : 0;
    return { preview, time, unread };
  };

  // Fetch agents on mount
  useEffect(() => {
    fetchAgents();
  }, []);

  const fetchAgents = async () => {
    setLoadingAgents(true);
    try {
      await refreshAgents();
      const currentAgents = useConfigStore.getState().agents;
      if (!agentId && currentAgents.length > 0) {
        setAgentId(currentAgents[0].agent_id);
      }
    } catch (err) {
      console.error('Failed to fetch agents:', err);
    } finally {
      setLoadingAgents(false);
    }
  };

  const handleSelectAgent = (id: string) => {
    if (id !== agentId) {
      setAgentId(id);
      setActiveAgent(id); // Also clears completion badge for this agent
    }
    // Always navigate back to chat when selecting an agent
    if (location.pathname !== '/app/chat' && location.pathname !== '/app') {
      navigate('/app/chat');
    }
  };

  const handleCreateAgent = async () => {
    await createAgent();
  };

  const handleTogglePublic = async (agent: typeof agents[0], e: React.MouseEvent) => {
    e.stopPropagation();
    const newIsPublic = !agent.is_public;
    try {
      const res = await api.updateAgent(agent.agent_id, undefined, undefined, newIsPublic);
      if (res.success) {
        setAgents(agents.map(a =>
          a.agent_id === agent.agent_id ? { ...a, is_public: newIsPublic } : a
        ));
      } else {
        console.error('Failed to toggle public:', res.error);
      }
    } catch (err) {
      console.error('Error toggling public:', err);
    }
  };

  const handleStartEdit = (agent: typeof agents[0], e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingAgentId(agent.agent_id);
    setEditingName(agent.name || agent.agent_id);
  };

  const handleCancelEdit = (e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingAgentId(null);
    setEditingName('');
  };

  const handleSaveEdit = async (targetAgentId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!editingName.trim()) return;

    setSavingName(true);
    try {
      const res = await api.updateAgent(targetAgentId, editingName.trim());
      if (res.success && res.agent) {
        setAgents(agents.map(a =>
          a.agent_id === targetAgentId
            ? { ...a, name: res.agent?.name }
            : a
        ));
        setEditingAgentId(null);
        setEditingName('');
      } else {
        console.error('Failed to update agent:', res.error);
      }
    } catch (err) {
      console.error('Error updating agent:', err);
    } finally {
      setSavingName(false);
    }
  };

  const handleDeleteAgent = async (agent: typeof agents[0], e: React.MouseEvent) => {
    e.stopPropagation();
    const ok = await confirm({
      title: 'Delete agent',
      message: `Delete agent "${agent.name || agent.agent_id}"? This will permanently remove all related data (narratives, events, instances, jobs, etc.).`,
      confirmText: 'Delete',
      danger: true,
    });
    if (!ok) return;

    setDeletingAgentId(agent.agent_id);
    try {
      const res = await api.deleteAgent(agent.agent_id);
      if (res.success) {
        const remaining = agents.filter(a => a.agent_id !== agent.agent_id);
        setAgents(remaining);
        clearAgent(agent.agent_id);
        if (agentId === agent.agent_id) {
          if (remaining.length > 0) {
            setAgentId(remaining[0].agent_id);
            setActiveAgent(remaining[0].agent_id);
          } else {
            setAgentId('');
          }
        }
      } else {
        console.error('Failed to delete agent:', res.error);
        await alert({
          title: 'Delete failed',
          message: `Failed to delete agent: ${res.error}`,
          danger: true,
        });
      }
    } catch (err) {
      console.error('Error deleting agent:', err);
      await alert({
        title: 'Delete failed',
        message: 'Error deleting agent. Please try again.',
        danger: true,
      });
    } finally {
      setDeletingAgentId(null);
    }
  };

  /** Render agent avatar — NM RingAvatar silicon (agents are AI) with
   * running spinner overlay or completion badge.
   *
   * Phase C (2026-05-13): "running" is now the OR of two signals:
   *   1. local WS streaming state for the current tab (legacy)
   *   2. backend BackgroundRun in 'running' state for this agent
   */
  const renderAgentAvatar = (
    agentLabel: string,
    id: string,
    hasBackendActiveRun: boolean = false,
    size: 'sm' | 'md' = 'md',
  ) => {
    const streaming = isAgentStreaming(id) || hasBackendActiveRun;

    if (streaming) {
      // Running state lives entirely on the avatar so the name row stays
      // clean (no inline "RUNNING" pill crowding / truncating the name):
      //   - a breathing halo ring (animate-ping) = the at-a-glance "this
      //     agent is working" cue, visible even in collapsed mode
      //   - the spinner replacing the ring center, as before
      return (
        <div className="relative inline-flex items-center justify-center">
          <span
            className="absolute inset-0 rounded-full animate-ping pointer-events-none"
            style={{ border: '2px solid var(--color-yellow-500)' }}
          />
          <RingAvatar species="silicon" label={agentLabel} size={size} />
          <Loader2 className="absolute w-3 h-3 animate-spin text-[var(--color-yellow-500)]" />
        </div>
      );
    }

    return <RingAvatar species="silicon" label={agentLabel} size={size} />;
  };

  // Collapsed mode: show compact agent icons
  if (collapsed) {
    return (
      <div className="p-2 space-y-2">
        <button
          onClick={handleCreateAgent}
          disabled={creatingAgent}
          className={cn(
            'w-full aspect-square rounded-xl flex items-center justify-center transition-all',
            'bg-[var(--bg-tertiary)] text-[var(--accent-primary)]',
            'hover:bg-[var(--accent-glow)] hover:shadow-[0_0_20px_var(--accent-glow)]',
            'border border-dashed border-[var(--accent-primary)]/30',
            creatingAgent && 'opacity-50 cursor-not-allowed'
          )}
          title="Create New Agent"
        >
          <Plus className={cn('w-5 h-5', creatingAgent && 'animate-pulse')} />
        </button>
        <button
          onClick={() => navigate('/app/manage-agents')}
          className="w-full aspect-square flex items-center justify-center border border-[var(--rule)] text-[var(--text-secondary)] hover:border-[var(--border-strong)] hover:text-[var(--text-primary)]"
          title="Manage agents (batch · add / edit / delete)"
          aria-label="Manage agents"
        >
          <ListChecks className="w-4 h-4" />
        </button>
        {agents.slice(0, 4).map((agent, index) => {
          const isSelected = agentId === agent.agent_id;
          const completed = completedAgentIds.includes(agent.agent_id);
          const label = (agent.name || agent.agent_id).slice(0, 2);
          return (
            <div key={agent.agent_id} className="relative flex justify-center">
              <button
                onClick={() => handleSelectAgent(agent.agent_id)}
                className={cn(
                  'p-1.5 rounded-full transition-colors duration-150 animate-fade-in',
                  isSelected ? 'bg-[var(--bg-elevated)]' : 'hover:bg-[var(--bg-elevated)]'
                )}
                style={{ animationDelay: `${index * 50}ms` }}
                title={agent.agent_id}
              >
                {renderAgentAvatar(label, agent.agent_id, !!agent.active_run, 'sm')}
              </button>
              {/* Completion badge dot */}
              {completed && !isSelected && (
                <div className="absolute -top-0.5 -right-0.5 w-2.5 h-2.5 rounded-full allow-circle bg-[var(--color-yellow-500)] border-2 border-[var(--bg-primary)]" />
              )}
            </div>
          );
        })}
      </div>
    );
  }

  // Expanded mode: full agent list
  return (
    <div>
      {confirmDialog}
      {/* Header — pinned to the top of the scroll viewport so the
          [ AGENTS ] label and its action icons stay anchored while the
          agent rows scroll underneath. `bg-[--nm-paper]` matches the
          sidebar paper so rows don't bleed through when they pass under
          the stuck header. Padding mirrors the original `p-3 px-1` so
          the visual placement is identical when not scrolling. */}
      <div className="sticky top-0 z-10 bg-[color:var(--nm-paper)] px-3 pt-3 pb-2">
        <div className="flex items-center justify-between px-1 gap-2">
          <BracketSectionLabel
            trailing={<span className="text-[10px] opacity-60">{agents.length}</span>}
          >
            Agents
          </BracketSectionLabel>
          <div className="flex items-center gap-1 shrink-0">
            <Button
              variant="ghost"
              size="icon"
              onClick={handleCreateAgent}
              disabled={creatingAgent}
              className="w-7 h-7"
              title="Create New Agent"
            >
              <Plus className={cn('w-3.5 h-3.5', creatingAgent && 'animate-pulse')} />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => navigate('/app/manage-agents')}
              className="w-7 h-7"
              title="Manage agents (batch · add / edit / delete)"
              aria-label="Manage agents"
            >
              <ListChecks className="w-3.5 h-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={fetchAgents}
              disabled={loadingAgents}
              className="w-7 h-7"
              title="Refresh Agents"
            >
              <RefreshCw className={cn('w-3 h-3', loadingAgents && 'animate-spin')} />
            </Button>
          </div>
        </div>
      </div>

      <div className="px-3 pb-3">
      {agents.length === 0 ? (
        <BracketEmptyState
          label="No agents yet"
          hint="Create your first agent to start a conversation."
          cta={
            <Button
              variant="outline"
              size="sm"
              onClick={handleCreateAgent}
              disabled={creatingAgent}
              className="gap-1.5"
            >
              <Plus className="w-3.5 h-3.5" />
              Create Agent
            </Button>
          }
        />
      ) : (
        /* NM messenger conversation list (FinChats:500-559 canonical).
           Outer container sits directly on the sidebar's paper bg with
           no wrapping panel. Each row is transparent by default; only
           when the agent is the active one OR has unread messages does
           the row get a silicon-soft (light-blue) rounded highlight.
           Radius is 18px per NM spec, no borders ever. */
        <div className="space-y-0.5">
          {agents.map((agent, index) => {
            const isSelected = agentId === agent.agent_id;
            const completed = completedAgentIds.includes(agent.agent_id);
            const { preview, time, unread } = getRowMeta(agent.agent_id);
            const displayName = agent.name || agent.agent_id;
            const isOwner = agent.created_by === userId;
            // Row bg priority: selected wins over unread. Selected uses a
            // theme-neutral ink overlay (--nm-row-active) so the highlight
            // relates to the underlying paper instead of stealing the
            // unread signal. Unread (only when not selected) keeps the
            // NM-canonical silicon-soft tint. Hover is paper-warm, only on
            // rows that are neither selected nor unread.
            const rowBg = isSelected
              ? 'var(--nm-row-active)'
              : unread > 0
                ? 'var(--color-silicon-soft)'
                : 'transparent';
            const allowHover = !isSelected && unread === 0;

            return (
              <div
                key={agent.agent_id}
                onClick={() => handleSelectAgent(agent.agent_id)}
                className={cn(
                  'w-full text-left px-3 py-2.5 cursor-pointer animate-slide-up',
                  'rounded-[18px] transition-colors duration-150',
                  'group',
                )}
                style={{
                  animationDelay: `${index * 50}ms`,
                  background: rowBg,
                }}
                onMouseEnter={(e) => {
                  if (allowHover) {
                    (e.currentTarget as HTMLDivElement).style.background = 'var(--nm-paper-warm)';
                  }
                }}
                onMouseLeave={(e) => {
                  if (allowHover) {
                    (e.currentTarget as HTMLDivElement).style.background = 'transparent';
                  }
                }}
              >
                <div className="flex items-start gap-3">
                  {/* Avatar — Silicon ring with streaming overlay; completion
                      dot stays on the avatar's top-right corner. */}
                  <div className="relative shrink-0">
                    {renderAgentAvatar(
                      displayName.slice(0, 2),
                      agent.agent_id,
                      !!agent.active_run,
                      'md',
                    )}
                    {completed && !isSelected && (
                      <div
                        className="absolute -top-1 -right-1 w-2.5 h-2.5 rounded-full allow-circle"
                        style={{
                          background: 'var(--color-warning)',
                          border: '2px solid var(--nm-card)',
                        }}
                      />
                    )}
                  </div>

                  {/* Right side: 2-row stack */}
                  <div className="flex-1 min-w-0">
                    {editingAgentId === agent.agent_id ? (
                      /* Inline rename mode — full width, no preview while editing */
                      <div className="flex items-center gap-1" onClick={e => e.stopPropagation()}>
                        <input
                          type="text"
                          value={editingName}
                          onChange={e => setEditingName(e.target.value)}
                          className="flex-1 min-w-0 px-2 py-0.5 text-sm font-mono text-[var(--nm-ink)] bg-[var(--nm-paper-warm)] border border-[var(--nm-ink)] rounded-[var(--radius-xs)] focus:outline-none"
                          autoFocus
                          onKeyDown={e => {
                            if (e.key === 'Enter') handleSaveEdit(agent.agent_id, e as any);
                            if (e.key === 'Escape') handleCancelEdit(e as any);
                          }}
                        />
                        <button
                          onClick={(e) => handleSaveEdit(agent.agent_id, e)}
                          disabled={savingName}
                          className="p-1 shrink-0 rounded-[var(--radius-xs)] hover:bg-[var(--nm-paper-warm)] transition-colors"
                          title="Save (Enter)"
                        >
                          <Check className={cn('w-3.5 h-3.5', savingName && 'animate-pulse')} style={{ color: 'var(--color-success)' }} />
                        </button>
                        <button
                          onClick={handleCancelEdit}
                          className="p-1 shrink-0 rounded-[var(--radius-xs)] hover:bg-[var(--nm-paper-warm)] transition-colors"
                          title="Cancel (Esc)"
                        >
                          <X className="w-3.5 h-3.5" style={{ color: 'var(--color-error)' }} />
                        </button>
                      </div>
                    ) : (
                      <>
                        {/* Row 1: [name] [inline action buttons] [..flex-spacer..] [time] */}
                        <div className="flex items-center gap-1.5">
                          <span
                            className={cn(
                              'text-sm truncate',
                              isSelected ? 'font-semibold' : 'font-medium'
                            )}
                            style={{
                              color: 'var(--nm-ink)',
                              fontFamily: 'var(--font-sans)',
                            }}
                          >
                            {displayName}
                          </span>
                          {/* Inline status flags after the name */}
                          {agent.is_public && !isOwner && (
                            <span title={`Public · by ${agent.created_by}`}>
                              <Globe className="w-3 h-3 shrink-0" style={{ color: 'var(--nm-ink50)' }} />
                            </span>
                          )}
                          {/* Running state is shown on the avatar (breathing
                              halo + spinner) — see renderAgentAvatar. No
                              inline badge here, so a long name keeps its full
                              width instead of being truncated by the badge. */}

                          {/* Owner-only inline action buttons; visible on hover
                              OR when this row is selected. */}
                          <div
                            className={cn(
                              'flex items-center gap-0.5 shrink-0',
                              isSelected ? 'opacity-100' : 'opacity-0 group-hover:opacity-100',
                              'transition-opacity duration-150'
                            )}
                          >
                            {SHOW_AGENT_PUBLIC_TOGGLE && isOwner && (
                              <button
                                onClick={(e) => handleTogglePublic(agent, e)}
                                className="p-1 rounded-[var(--radius-xs)] hover:bg-[var(--nm-paper-warm)] transition-colors"
                                title={agent.is_public ? 'Set to Private' : 'Set to Public'}
                              >
                                {agent.is_public ? (
                                  <Globe className="w-3 h-3" style={{ color: 'var(--nm-ink)' }} />
                                ) : (
                                  <Lock className="w-3 h-3" style={{ color: 'var(--nm-ink50)' }} />
                                )}
                              </button>
                            )}
                            <button
                              onClick={(e) => handleStartEdit(agent, e)}
                              className="p-1 rounded-[var(--radius-xs)] hover:bg-[var(--nm-paper-warm)] transition-colors"
                              title="Edit name"
                            >
                              <Pencil className="w-3 h-3" style={{ color: 'var(--nm-ink50)' }} />
                            </button>
                            {isOwner && (
                              <button
                                onClick={(e) => handleDeleteAgent(agent, e)}
                                disabled={deletingAgentId === agent.agent_id}
                                className="p-1 rounded-[var(--radius-xs)] hover:bg-[var(--nm-paper-warm)] transition-colors"
                                title="Delete agent"
                              >
                                <Trash2 className={cn('w-3 h-3', deletingAgentId === agent.agent_id && 'animate-pulse')} style={{ color: 'var(--color-error)' }} />
                              </button>
                            )}
                          </div>

                          {/* Right-aligned timestamp. NM: unread rows use
                              the species color (silicon) for time, read use
                              ink50. Tabular-nums so the column doesn't shift
                              when the time string changes. */}
                          <span
                            className="ml-auto pl-2 text-[10px] shrink-0"
                            style={{
                              color: unread > 0 ? 'var(--color-silicon)' : 'var(--nm-ink50)',
                              fontWeight: unread > 0 ? 500 : 400,
                              fontFamily: 'var(--font-mono)',
                              fontVariantNumeric: 'tabular-nums',
                            }}
                          >
                            {time}
                          </span>
                        </div>

                        {/* Row 2: [preview ...truncated] [unread pill]. The
                            unread pill is the NM count pattern: transparent
                            bg, ink30 hairline, ink70 mono digit — NOT a
                            saturated species-color chip (FinChats:546-552). */}
                        <div className="flex items-center gap-2 mt-0.5">
                          <p
                            className="flex-1 min-w-0 text-xs truncate leading-snug"
                            style={{ color: 'var(--nm-ink70)' }}
                          >
                            {preview || (
                              <span style={{ color: 'var(--nm-ink30)' }}>
                                {agent.description || 'No messages yet'}
                              </span>
                            )}
                          </p>
                          {unread > 0 && (
                            <span
                              className="inline-flex items-center justify-center text-[10px] font-semibold shrink-0"
                              style={{
                                minWidth: 20,
                                height: 18,
                                padding: '0 6px',
                                borderRadius: 9,
                                background: 'transparent',
                                border: '1px solid var(--nm-ink30)',
                                color: 'var(--nm-ink70)',
                                fontFamily: 'var(--font-mono)',
                                letterSpacing: '0.02em',
                              }}
                            >
                              {unread > 99 ? '99+' : unread}
                            </span>
                          )}
                        </div>
                      </>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
      </div>
    </div>
  );
}
