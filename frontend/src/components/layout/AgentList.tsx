/**
 * @file_name: AgentList.tsx
 * @author:
 * @date: 2026-06-10
 * @description: The sidebar's Chats list — team rows + a flat agent list
 * with collapsible sections, unread + running indicators. Each owned agent
 * row has a ⋯ menu — Rename / Model & framework / Delete — an OWNER-REQUIRED
 * entry reinstated 2026-09-11 after #383 moved every agent action to the
 * profile page; the profile page stays a second door (and the only one for
 * Clear data / description). Rename and delete run through the same
 * `useAgentActions` hook as the profile page; Model & framework opens the
 * same AgentLlmConfigPanel. Team rows keep their own ⋮ menu. Creation, import
 * and export moved to the sidebar's global nav (Chat UI v4).
 */

import { useState, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useLocation } from 'react-router-dom';
import {
  RefreshCw,
  Plus,
  Search,
  ChevronRight,
} from 'lucide-react';
import { Button, useConfirm } from '@/components/ui';
import { BracketSectionLabel, BracketEmptyState } from '@/components/nm';
import { useConfigStore, useChatStore, useTeamsStore, useUIStore } from '@/stores';
import { useCreateAgent, useAgentActions } from '@/hooks';
import { AgentLlmConfigPanel } from '@/components/chat/AgentLlmConfigPanel';
import { cn, formatChatTimestamp } from '@/lib/utils';
import { getLastReadMs, markAgentRead, countUnread, latestMessageMs, markTeamRead } from '@/lib/unread';
import { AgentGroupSection, type AgentRowActions } from './AgentGroupSection';
import { sortAgentsByActivity } from './agentGroupUtils';
import { TeamChatRow } from './TeamChatRow';

/** Small collapsible category header (TEAMS / AGENTS) in the sidebar list. */
function CategoryHeader({
  label,
  count,
  collapsed,
  onToggle,
}: {
  label: string;
  count: number;
  collapsed: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className="w-full flex items-center gap-2 px-3 py-1.5 text-left transition-colors hover:bg-[var(--nm-paper-warm)]"
    >
      <span
        className="flex-1 min-w-0 text-[11px] font-mono uppercase tracking-wider truncate"
        style={{ color: 'var(--nm-ink50)' }}
      >
        {label}
      </span>
      <span className="text-[10px] font-mono shrink-0" style={{ color: 'var(--nm-ink30)' }}>
        {count}
      </span>
      {/* Lucide chevron, not a glyph — same linear icon language as the
          team-row toggle (design_system.md §5: no solid/linear mixing). */}
      <ChevronRight
        className={cn(
          'h-3 w-3 shrink-0 transition-transform duration-150',
          !collapsed && 'rotate-90',
        )}
        style={{ color: 'var(--nm-ink30)' }}
        aria-hidden
      />
    </button>
  );
}

export function AgentList() {
  const { t } = useTranslation();
  const [loadingAgents, setLoadingAgents] = useState(false);
  const { createAgent, creating: creatingAgent } = useCreateAgent();
  // Collapse state for the TEAMS / AGENTS sidebar categories (persisted).
  const [teamsCollapsed, setTeamsCollapsed] = useState(
    () => typeof window !== 'undefined' && localStorage.getItem('sidebar_cat_teams') === '1',
  );
  const [agentsCollapsed, setAgentsCollapsed] = useState(
    () => typeof window !== 'undefined' && localStorage.getItem('sidebar_cat_agents') === '1',
  );
  const setCatCollapsed = (cat: 'teams' | 'agents', v: boolean) => {
    if (cat === 'teams') setTeamsCollapsed(v);
    else setAgentsCollapsed(v);
    try {
      localStorage.setItem(cat === 'teams' ? 'sidebar_cat_teams' : 'sidebar_cat_agents', v ? '1' : '0');
    } catch { /* storage unavailable — collapse just won't persist */ }
  };

  const navigate = useNavigate();
  const location = useLocation();
  const { userId, agentId, agents: rawAgents, setAgentId, refreshAgents } = useConfigStore();
  const { setActiveAgent, isAgentStreaming, completedAgentIds } =
    useChatStore();
  const agentSessions = useChatStore((s) => s.agentSessions);
  const teams = useTeamsStore((s) => s.teams);
  const teamsLoaded = useTeamsStore((s) => s.loaded);
  const teamsRefresh = useTeamsStore((s) => s.refresh);
  const teamsUpdate = useTeamsStore((s) => s.updateTeam);
  const teamsDelete = useTeamsStore((s) => s.deleteTeam);
  const { confirm, alert, dialog: confirmDialog } = useConfirm();
  const setPaletteOpen = useUIStore((s) => s.setPaletteOpen);
  const { renameAgent, deleteAgent } = useAgentActions({ confirm, alert });
  // The agent whose model & framework panel is open from its row's ⋯ menu.
  const [configAgentId, setConfigAgentId] = useState<string | null>(null);

  const rowActions: AgentRowActions = {
    onRename: (id, name) => { void renameAgent(id, name); },
    onOpenModelConfig: setConfigAgentId,
    onDelete: (id) => {
      void (async () => {
        const target = rawAgents.find((a) => a.agent_id === id);
        const { deleted, wasActive } = await deleteAgent(id, target?.name || id);
        // Same landing as the profile page's delete: the view that showed the
        // deleted agent is gone, so go to the Dashboard (its default tab).
        // Deleting some OTHER agent leaves the current view alone.
        if (deleted && wasActive) navigate('/app/dashboard');
      })();
    },
  };

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

  /**
   * Cheap per-render projection of ONLY what can change sort order: each
   * agent's id + committed-message count + last message time. Streaming
   * deltas rebuild the `agentSessions` object every token but mutate
   * `currentEvents` / `currentAssistantMessage`, NOT `messages` (see
   * chatStore.updateSession) — so this string is byte-identical across the
   * per-token churn. It's O(n) (reads length + tail element, no full scan)
   * and gates the O(n·m) sort below to re-run only when a message is actually
   * committed. Long sessions (铁律 #14) make the avoided work grow, and 铁律
   * #16 says the platform must not become the interruption source: keeping
   * the sidebar off the streaming hot path honors both.
   */
  const activitySignature = rawAgents
    .map((a) => {
      const msgs = agentSessions[a.agent_id]?.messages;
      const last = msgs && msgs.length ? msgs[msgs.length - 1] : undefined;
      return `${a.agent_id}:${msgs?.length ?? 0}:${last?.timestamp ?? 0}`;
    })
    .join('|');

  /**
   * Agents ordered so the most-recently-active conversation floats to the top
   * ("recently chatted agent auto-pins"). The activity time blends the
   * server's last assistant reply with the freshest LOCAL session message, so
   * an agent jumps to the top the instant you talk to it — before the next
   * /api/auth/agents refresh.
   */
  const sortedAgents = useMemo(
    () =>
      sortAgentsByActivity(rawAgents, (aid) =>
        latestMessageMs(agentSessions[aid]?.messages ?? []),
      ),
    // agentSessions is intentionally NOT a dep: activitySignature is its
    // sort-relevant projection. The closure still reads the current render's
    // agentSessions, which is fresh on every render where the signature (and
    // therefore the sort result) could have changed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rawAgents, activitySignature],
  );

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

  // Zero-agent empty state. Routed through the creation studio's fork, not
  // straight into a blank agent: this is the first-run user the fork exists
  // for. Team-scoped creation below stays direct — that flow already has a
  // destination and a reason.
  const handleCreateAgent = () => {
    navigate('/app/agents/new');
  };

  // #43: create a new agent already assigned to this team; useCreateAgent
  // opens the team's group chat so the membership change is immediately
  // visible. The entry point now lives in the TEAMS-row ⋮ menu (TeamRowMenu)
  // since the old AgentGroupSection-header "+" no longer exists in the
  // TEAMS/AGENTS layout.
  const handleCreateAgentInTeam = async (teamId: string) => {
    await createAgent({ teamId });
  };

  const handleDeleteTeam = async (teamId: string) => {
    const team = teams.find((x) => x.team.team_id === teamId);
    const ok = await confirm({
      title: t('layout.agentList.deleteTeamTitle'),
      message: t('layout.agentList.deleteTeamMessage', { name: team?.team.name ?? teamId }),
      confirmText: t('layout.agentList.deleteAction'),
      danger: true,
    });
    if (!ok) return;
    try {
      await teamsDelete(teamId);
      // If the deleted team's chat/detail is open, fall back to the chat view.
      if (location.pathname.startsWith(`/app/teams/${teamId}`)) {
        navigate('/app/chat');
      }
    } catch (err) {
      await alert({
        title: t('layout.agentList.deleteFailedTitle'),
        message: err instanceof Error ? err.message : String(err),
        danger: true,
      });
    }
  };

  // Which team's group chat is open (route /app/teams/:id/chat) — drives the
  // active highlight on the Group chat row and suppresses agent-row selection.
  const teamChatMatch = location.pathname.match(/^\/app\/teams\/([^/]+)\/chat$/);
  const activeTeamChatId = teamChatMatch ? teamChatMatch[1] : null;

  // Opening a room clears its mark DURABLY: the watermark goes to localStorage,
  // so it stays cleared after navigating away (dev's team-unread logic, merged
  // 2026-08-18). The v4 team row currently shows no unread dot — Owner ruling
  // kept the v4 row layout — but the watermark is maintained so wiring a dot
  // back is a one-prop change, and the panel's own advancing stays monotonic
  // with this one.
  const activeTeamLastMessageAt =
    teams.find((t) => t.team.team_id === activeTeamChatId)?.last_message_at ?? null;
  useEffect(() => {
    if (!activeTeamChatId || !activeTeamLastMessageAt) return;
    // An unparseable timestamp is NaN, which markTeamRead already refuses.
    markTeamRead(activeTeamChatId, Date.parse(activeTeamLastMessageAt));
  }, [activeTeamChatId, activeTeamLastMessageAt]);

  return (
    <div>
      {confirmDialog}
      {configAgentId && (
        <AgentLlmConfigPanel
          agentId={configAgentId}
          isOpen
          onClose={() => setConfigAgentId(null)}
          onSaved={() => void refreshAgents()}
        />
      )}
      {/* Header — v4: label + count with search (⌘K palette) and refresh.
          Creation / import / export moved to the sidebar's global nav. */}
      <div className="sticky top-0 z-10 bg-[color:var(--nm-paper)] px-3 pt-2.5 pb-1.5">
        <div className="flex items-center justify-between px-1 gap-2">
          <span data-help-id="sidebar.agent-list">
            <BracketSectionLabel
              trailing={<span className="text-[10px] opacity-60">{teams.length + rawAgents.length}</span>}
            >
              {t('sidebar.chats')}
            </BracketSectionLabel>
          </span>
          <div className="flex items-center gap-1 shrink-0">
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setPaletteOpen(true)}
              className="w-7 h-7"
              title={t('sidebar.searchChatsTitle')}
              aria-label={t('sidebar.searchChatsTitle')}
            >
              <Search className="w-3.5 h-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={fetchAgents}
              disabled={loadingAgents}
              className="w-7 h-7"
              title={t('layout.agentList.refreshAgents')}
            >
              <RefreshCw className={cn('w-3 h-3', loadingAgents && 'animate-spin')} />
            </Button>
          </div>
        </div>
      </div>

      <div className="px-1 pb-3">
        {rawAgents.length === 0 && teams.length === 0 ? (
          <BracketEmptyState
            label={t('layout.agentList.emptyLabel')}
            hint={t('layout.agentList.emptyHint')}
            cta={
              <Button
                variant="outline"
                size="sm"
                onClick={handleCreateAgent}
                disabled={creatingAgent}
                className="gap-1.5"
              >
                <Plus className="w-3.5 h-3.5" />
                {t('layout.agentList.createAgent')}
              </Button>
            }
          />
        ) : (
          <div className="space-y-1">
            {/* TEAMS — group chats, collected at the top (one row per team). */}
            {teams.length > 0 && (
              <div>
                <CategoryHeader
                  label={t('sidebar.teams')}
                  count={teams.length}
                  collapsed={teamsCollapsed}
                  onToggle={() => setCatCollapsed('teams', !teamsCollapsed)}
                />
                {!teamsCollapsed && (
                  <div className="space-y-0.5 px-1 pb-1">
                    {teams.map((t) => (
                      <TeamChatRow
                        key={t.team.team_id}
                        teamId={t.team.team_id}
                        teamName={t.team.name}
                        agentCount={t.member_agent_ids.length}
                        active={activeTeamChatId === t.team.team_id}
                        members={t.member_agent_ids.map((aid) => ({
                          agentId: aid,
                          name: rawAgents.find((a) => a.agent_id === aid)?.name || aid,
                        }))}
                        activeAgentId={activeTeamChatId ? null : agentId}
                        onSelectMember={handleSelectAgent}
                        onOpen={(tid) => navigate(`/app/teams/${tid}/chat`)}
                        onRename={(tid, name) => { void teamsUpdate(tid, { name }); }}
                        onDelete={handleDeleteTeam}
                        onAddAgent={handleCreateAgentInTeam}
                        addingAgent={creatingAgent}
                      />
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* AGENTS — every agent once, flat (no per-team duplication). */}
            <div>
              <CategoryHeader
                label={t('sidebar.agents')}
                count={rawAgents.length}
                collapsed={agentsCollapsed}
                onToggle={() => setCatCollapsed('agents', !agentsCollapsed)}
              />
              {!agentsCollapsed && (
                rawAgents.length === 0 ? (
                  <div className="px-3 py-2 text-xs" style={{ color: 'var(--nm-ink50)' }}>
                    {t('layout.agentList.noAgentsShort')}
                  </div>
                ) : (
                  <AgentGroupSection
                    teamId={null}
                    teamName=""
                    teamColor={null}
                    agents={sortedAgents}
                    agentId={agentId}
                    activeTeamChatId={activeTeamChatId}
                    collapsed={false}
                    hideHeader
                    onToggleCollapse={() => {}}
                    onSelectAgent={handleSelectAgent}
                    getRowMeta={getRowMeta}
                    getIsStreaming={getIsStreaming}
                    completedAgentIds={completedAgentIds}
                    currentUserId={userId}
                    rowActions={rowActions}
                  />
                )
              )}
            </div>
          </div>
        )}
      </div>

    </div>
  );
}
