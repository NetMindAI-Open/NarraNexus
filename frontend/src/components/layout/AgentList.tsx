/**
 * @file_name: AgentList.tsx
 * @author:
 * @date: 2026-06-10
 * @description: Agent selection, creation, editing, and management.
 * Shows agents grouped by team with collapsible sections. Running
 * indicators and completion badges support multi-agent concurrent chat.
 */

import { useState, useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import {
  RefreshCw,
  Plus,
  ListChecks,
} from 'lucide-react';
import { Button, useConfirm } from '@/components/ui';
import { BracketSectionLabel, BracketEmptyState } from '@/components/nm';
import { useConfigStore, useChatStore, useTeamsStore } from '@/stores';
import { useCreateAgent } from '@/hooks';
import { api } from '@/lib/api';
import { cn, formatChatTimestamp } from '@/lib/utils';
import { getLastReadMs, markAgentRead, countUnread, latestMessageMs } from '@/lib/unread';
import {
  buildAgentGroups,
  getCollapsedState,
  setCollapsedState,
} from './agentGroupUtils';
import type { CollapsedState } from './agentGroupUtils';
import { AgentGroupSection, AvatarWithStreaming } from './AgentGroupSection';
import { AgentsHeaderMenu } from './AgentsHeaderMenu';
import { TeamManagementModal } from '@/components/teams/TeamManagementModal';

interface AgentListProps {
  collapsed: boolean;
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

export function AgentList({ collapsed }: AgentListProps) {
  const [loadingAgents, setLoadingAgents] = useState(false);
  const { createAgent, creating: creatingAgent } = useCreateAgent();
  const [editingAgentId, setEditingAgentId] = useState<string | null>(null);
  const [editingName, setEditingName] = useState('');
  const [savingName, setSavingName] = useState(false);
  const [deletingAgentId, setDeletingAgentId] = useState<string | null>(null);
  const [sectionCollapsed, setSectionCollapsed] = useState<CollapsedState>(() => getCollapsedState());
  const [openMgmt, setOpenMgmt] = useState(false);

  const navigate = useNavigate();
  const location = useLocation();
  const { userId, agentId, agents: rawAgents, setAgentId, setAgents, refreshAgents } = useConfigStore();
  const { setActiveAgent, clearAgent, isAgentStreaming, completedAgentIds } = useChatStore();
  const agentSessions = useChatStore((s) => s.agentSessions);
  const teams = useTeamsStore((s) => s.teams);
  const teamsLoaded = useTeamsStore((s) => s.loaded);
  const teamsRefresh = useTeamsStore((s) => s.refresh);
  const { confirm, alert, dialog: confirmDialog } = useConfirm();

  // Ensure teams are loaded so grouping is accurate.
  useEffect(() => {
    if (!teamsLoaded) teamsRefresh();
  }, [teamsLoaded, teamsRefresh]);

  // Mark the active agent's messages as read so its unread count stays
  // cleared after the user navigates away.
  useEffect(() => {
    if (!agentId) return;
    const latest = latestMessageMs(agentSessions[agentId]?.messages ?? []);
    if (latest > 0) markAgentRead(agentId, latest);
  }, [agentId, agentSessions]);

  /**
   * Derive the per-agent meta shown in each row: agent-reply preview +
   * activity time and an unread count.
   *
   * Preview is the most recent assistant message — NM messenger UX:
   * each row "belongs" to the agent, so the second line previews what
   * the agent last said to the user, not what the user just typed.
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
    const time = timeMs ? formatChatTimestamp(timeMs) : '';
    const unread = aid !== agentId ? countUnread(messages, getLastReadMs(aid)) : 0;
    return { preview, time, unread };
  };

  const getIsStreaming = (aid: string) => isAgentStreaming(aid);

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
      setActiveAgent(id);
    }
    if (location.pathname !== '/app/chat' && location.pathname !== '/app') {
      navigate('/app/chat');
    }
  };

  const handleCreateAgent = async () => {
    await createAgent();
  };

  const handleTogglePublic = async (agent: typeof rawAgents[0], e: React.MouseEvent) => {
    e.stopPropagation();
    const newIsPublic = !agent.is_public;
    try {
      const res = await api.updateAgent(agent.agent_id, undefined, undefined, newIsPublic);
      if (res.success) {
        setAgents(rawAgents.map(a =>
          a.agent_id === agent.agent_id ? { ...a, is_public: newIsPublic } : a
        ));
      } else {
        console.error('Failed to toggle public:', res.error);
      }
    } catch (err) {
      console.error('Error toggling public:', err);
    }
  };

  const handleStartEdit = (agent: typeof rawAgents[0], e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingAgentId(agent.agent_id);
    setEditingName(agent.name || agent.agent_id);
  };

  const handleCancelEdit = (e: React.SyntheticEvent) => {
    e.stopPropagation();
    setEditingAgentId(null);
    setEditingName('');
  };

  const handleSaveEdit = async (targetAgentId: string, e: React.SyntheticEvent) => {
    e.stopPropagation();
    if (!editingName.trim()) return;

    setSavingName(true);
    try {
      const res = await api.updateAgent(targetAgentId, editingName.trim());
      if (res.success && res.agent) {
        setAgents(rawAgents.map(a =>
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

  const handleDeleteAgent = async (agent: typeof rawAgents[0], e: React.MouseEvent) => {
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
        const remaining = rawAgents.filter(a => a.agent_id !== agent.agent_id);
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

  const handleToggleSection = (teamId: string | null) => {
    const key = teamId ?? '__ungrouped__';
    const next = !sectionCollapsed[key];
    setSectionCollapsed((prev) => ({ ...prev, [key]: next }));
    setCollapsedState(key, next);
  };

  const handleImport = () => navigate('/app/bundle/import');
  const handleExport = () => {
    // Pre-fill export wizard with agents if a team context is relevant.
    navigate('/app/bundle/export');
  };
  const handleManageTeams = () => setOpenMgmt(true);

  // Grouped sections — shared by the expanded list and the collapsed
  // avatar rail (the rail draws a hairline between team groups).
  const groups = buildAgentGroups(rawAgents, teams);

  // Collapsed mode: avatar rail — EVERY agent across all groups (spec §11.2;
  // the old rail silently capped at 4). The rail's job is fast agent
  // switching: RingAvatar + unread badge, hairline divider between teams,
  // no team chips / filter glyphs.
  if (collapsed) {
    const nonEmptyGroups = groups.filter((g) => g.agents.length > 0);
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
        {nonEmptyGroups.map((group, gi) => (
          <div key={group.teamId ?? '__ungrouped__'} className="space-y-2">
            {gi > 0 && (
              <div className="mx-2 border-t border-[var(--nm-hairline)]" aria-hidden />
            )}
            {group.agents.map((agent) => {
              const isSelected = agentId === agent.agent_id;
              const completed = completedAgentIds.includes(agent.agent_id);
              const label = (agent.name || agent.agent_id).slice(0, 2);
              const streaming = isAgentStreaming(agent.agent_id) || !!agent.active_run;
              const { unread } = getRowMeta(agent.agent_id);
              return (
                <div key={agent.agent_id} className="relative flex justify-center">
                  <button
                    onClick={() => handleSelectAgent(agent.agent_id)}
                    className={cn(
                      'p-1.5 rounded-full transition-colors duration-150',
                      isSelected ? 'bg-[var(--bg-elevated)]' : 'hover:bg-[var(--bg-elevated)]'
                    )}
                    title={agent.name || agent.agent_id}
                    aria-label={agent.name || agent.agent_id}
                    aria-current={isSelected ? 'true' : undefined}
                  >
                    <AvatarWithStreaming label={label} streaming={streaming} size="sm" />
                  </button>
                  {unread > 0 && (
                    <span
                      className="absolute -top-0.5 -right-0.5 min-w-[16px] h-4 px-1 rounded-[8px] flex items-center justify-center text-[9px] font-mono"
                      style={{
                        background: 'var(--nm-card)',
                        border: '1px solid var(--nm-ink30)',
                        color: 'var(--nm-ink70)',
                      }}
                    >
                      {unread > 9 ? '9+' : unread}
                    </span>
                  )}
                  {completed && !isSelected && unread === 0 && (
                    <div className="absolute -top-0.5 -right-0.5 w-2.5 h-2.5 rounded-full allow-circle bg-[var(--color-yellow-500)] border-2 border-[var(--bg-primary)]" />
                  )}
                </div>
              );
            })}
          </div>
        ))}
      </div>
    );
  }

  // Expanded mode: grouped agent list
  return (
    <div>
      {confirmDialog}
      <TeamManagementModal open={openMgmt} onClose={() => setOpenMgmt(false)} />

      {/* Header */}
      <div className="sticky top-0 z-10 bg-[color:var(--nm-paper)] px-3 pt-3 pb-2">
        <div className="flex items-center justify-between px-1 gap-2">
          <span data-help-id="sidebar.agent-list">
            <BracketSectionLabel
              trailing={<span className="text-[10px] opacity-60">{rawAgents.length}</span>}
            >
              Agents
            </BracketSectionLabel>
          </span>
          <div className="flex items-center gap-1 shrink-0">
            <span data-help-id="sidebar.create-agent">
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
            </span>
            <span data-help-id="sidebar.manage-agents">
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
            </span>
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
            <span data-help-id="sidebar.agents-menu">
              <AgentsHeaderMenu
                onImport={handleImport}
                onExport={handleExport}
                onManageTeams={handleManageTeams}
              />
            </span>
          </div>
        </div>
      </div>

      <div className="px-1 pb-3">
        {rawAgents.length === 0 ? (
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
          <div>
            {groups.map((group) => {
              // Pure no-teams scenario (single Ungrouped group): render the
              // rows flat — an "Ungrouped" header with nothing to contrast
              // against is noise.
              const isOnlyGroup = groups.length === 1 && group.teamId === null;

              // Skip Ungrouped section if it's empty (other groups have all agents).
              if (group.teamId === null && group.agents.length === 0) return null;

              const key = group.teamId ?? '__ungrouped__';
              const isSectionCollapsed = !!sectionCollapsed[key];

              return (
                <AgentGroupSection
                  key={key}
                  teamId={group.teamId}
                  teamName={group.teamName}
                  teamColor={group.teamColor}
                  agents={group.agents}
                  agentId={agentId}
                  collapsed={isSectionCollapsed}
                  hideHeader={isOnlyGroup}
                  onToggleCollapse={handleToggleSection}
                  onSelectAgent={handleSelectAgent}
                  getRowMeta={getRowMeta}
                  getIsStreaming={getIsStreaming}
                  completedAgentIds={completedAgentIds}
                  currentUserId={userId}
                  showPublicToggle={SHOW_AGENT_PUBLIC_TOGGLE}
                  onNavigateToTeam={(tid) => navigate(`/app/teams/${tid}`)}
                  editingAgentId={editingAgentId}
                  editingName={editingName}
                  onEditNameChange={setEditingName}
                  onSaveEdit={handleSaveEdit}
                  onCancelEdit={handleCancelEdit}
                  savingName={savingName}
                  onStartEdit={handleStartEdit}
                  onDelete={handleDeleteAgent}
                  onTogglePublic={handleTogglePublic}
                  deletingAgentId={deletingAgentId}
                />
              );
            })}
          </div>
        )}
      </div>

    </div>
  );
}
