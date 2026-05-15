/**
 * TeamFilterBar — sidebar header showing team chips for filtering AgentList.
 *
 * Subproject 1: All / <team chips...> / Untagged.
 * Click "All" to show all agents, click a team chip to scope, click the gear
 * icon to open TeamManagementModal.
 */

import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Settings2, ExternalLink, Upload, Package } from 'lucide-react';
import { useTeamsStore, useConfigStore } from '@/stores';
import { TeamManagementModal } from '@/components/teams/TeamManagementModal';
import { cn } from '@/lib/utils';

interface Props {
  selectedFilter: string;            // 'all' | 'untagged' | <team_id>
  onChange: (filter: string) => void;
  collapsed?: boolean;
}

export function TeamFilterBar({ selectedFilter, onChange, collapsed }: Props) {
  const { teams, refresh, loaded } = useTeamsStore();
  const { agents } = useConfigStore();
  const [openMgmt, setOpenMgmt] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    if (!loaded) refresh();
  }, [loaded, refresh]);

  const untaggedCount = useMemo(() => {
    const memberSet = new Set<string>();
    teams.forEach((t) => t.member_agent_ids.forEach((id) => memberSet.add(id)));
    return agents.filter((a) => !memberSet.has(a.agent_id)).length;
  }, [teams, agents]);

  // When the user clicks the Package shortcut, pre-fill the wizard with the
  // currently filtered team (if any). Picks up team_id + member agent_ids
  // exactly the way TeamDetailPage's Export button does, so the wizard knows
  // both the team binding (for default filename / intro) and the agent
  // closure to scope below tabs.
  function gotoExport() {
    const team = teams.find((t) => t.team.team_id === selectedFilter);
    if (team) {
      const agentsCsv = team.member_agent_ids.join(',');
      const search = new URLSearchParams({ team: team.team.team_id });
      if (agentsCsv) search.set('agents', agentsCsv);
      navigate(`/app/bundle/export?${search.toString()}`);
    } else {
      navigate('/app/bundle/export');
    }
  }
  const exportTitle = (() => {
    const team = teams.find((t) => t.team.team_id === selectedFilter);
    return team
      ? `Export "${team.team.name}" as .nxbundle`
      : 'Export agents as .nxbundle';
  })();

  if (collapsed) {
    // Compact rail: stacked color dots, click cycles, gear opens modal.
    return (
      <div className="px-2 pt-2 pb-1 border-b border-[var(--border-subtle)] flex flex-col items-center gap-1.5">
        <button
          onClick={() => setOpenMgmt(true)}
          className="p-1 hover:bg-[var(--bg-tertiary)]"
          title="Manage teams"
        >
          <Settings2 className="w-3.5 h-3.5 text-[var(--text-tertiary)]" />
        </button>
        <button
          onClick={() => navigate('/app/bundle/import')}
          className="p-1 hover:bg-[var(--bg-tertiary)]"
          title="Import a .nxbundle (team)"
        >
          <Upload className="w-3.5 h-3.5 text-[var(--text-tertiary)]" />
        </button>
        <button
          onClick={gotoExport}
          className="p-1 hover:bg-[var(--bg-tertiary)]"
          title={exportTitle}
        >
          <Package className="w-3.5 h-3.5 text-[var(--text-tertiary)]" />
        </button>
        <button
          onClick={() => onChange('all')}
          title={`All (${agents.length})`}
          className={cn(
            'w-6 h-6 flex items-center justify-center text-[9px] font-mono border',
            selectedFilter === 'all'
              ? 'bg-[var(--bg-elevated)] border-[var(--border-strong)]'
              : 'border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]'
          )}
        >∗</button>
        {teams.map((t) => (
          <button
            key={t.team.team_id}
            onClick={() => onChange(t.team.team_id)}
            title={`${t.team.name} (${t.member_agent_ids.length})`}
            className={cn(
              'w-6 h-6 flex items-center justify-center border',
              selectedFilter === t.team.team_id
                ? 'border-[var(--border-strong)]'
                : 'border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]'
            )}
          >
            <span className="w-3 h-3 rounded-full" style={{ backgroundColor: t.team.color || '#666' }} />
          </button>
        ))}
        <button
          onClick={() => onChange('untagged')}
          title={`Untagged (${untaggedCount})`}
          className={cn(
            'w-6 h-6 flex items-center justify-center text-[9px] font-mono border',
            selectedFilter === 'untagged'
              ? 'bg-[var(--bg-elevated)] border-[var(--border-strong)]'
              : 'border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]'
          )}
        >∅</button>
        <TeamManagementModal open={openMgmt} onClose={() => setOpenMgmt(false)} />
      </div>
    );
  }

  return (
    <div className="px-3 pt-3 pb-2 border-b border-[var(--border-subtle)] space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-semibold text-[var(--text-tertiary)] uppercase tracking-[0.15em] font-[family-name:var(--font-mono)]">
          Teams
        </span>
        <div className="flex items-center gap-0.5">
          <button
            onClick={() => navigate('/app/bundle/import')}
            className="p-1 hover:bg-[var(--bg-tertiary)]"
            title="Import a .nxbundle (team)"
          >
            <Upload className="w-3.5 h-3.5 text-[var(--text-tertiary)]" />
          </button>
          <button
            onClick={gotoExport}
            className="p-1 hover:bg-[var(--bg-tertiary)]"
            title={exportTitle}
          >
            <Package className="w-3.5 h-3.5 text-[var(--text-tertiary)]" />
          </button>
          <button
            onClick={() => setOpenMgmt(true)}
            className="p-1 hover:bg-[var(--bg-tertiary)]"
            title="Manage teams"
          >
            <Settings2 className="w-3.5 h-3.5 text-[var(--text-tertiary)]" />
          </button>
        </div>
      </div>
      <div className="flex flex-wrap gap-1">
        <Chip
          label="All"
          count={agents.length}
          active={selectedFilter === 'all'}
          onClick={() => onChange('all')}
        />
        {teams.map((t) => (
          <Chip
            key={t.team.team_id}
            label={t.team.name}
            count={t.member_agent_ids.length}
            active={selectedFilter === t.team.team_id}
            color={t.team.color}
            onClick={() => onChange(t.team.team_id)}
            onDoubleClick={() => navigate(`/app/teams/${t.team.team_id}`)}
            navIcon={selectedFilter === t.team.team_id}
            onNavigate={() => navigate(`/app/teams/${t.team.team_id}`)}
          />
        ))}
        <Chip
          label="Untagged"
          count={untaggedCount}
          active={selectedFilter === 'untagged'}
          onClick={() => onChange('untagged')}
        />
      </div>
      <TeamManagementModal open={openMgmt} onClose={() => setOpenMgmt(false)} />
    </div>
  );
}

function Chip({ label, count, active, color, onClick, onDoubleClick, navIcon, onNavigate }: {
  label: string;
  count: number;
  active: boolean;
  color?: string | null;
  onClick: () => void;
  onDoubleClick?: () => void;
  navIcon?: boolean;
  onNavigate?: () => void;
}) {
  return (
    <div
      className={cn(
        'flex items-center gap-1 px-2 py-1 text-[11px] font-mono border transition-colors cursor-pointer',
        active
          ? 'bg-[var(--bg-elevated)] border-[var(--border-strong)] text-[var(--text-primary)]'
          : 'border-[var(--border-subtle)] text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)]'
      )}
      onClick={onClick}
      onDoubleClick={onDoubleClick}
      title={onDoubleClick ? `Click to filter, double-click to open ${label}` : label}
    >
      {color && <span className="w-2 h-2 rounded-full" style={{ backgroundColor: color }} />}
      <span>{label}</span>
      <span className="text-[var(--text-tertiary)]">({count})</span>
      {navIcon && onNavigate && (
        <button
          onClick={(e) => { e.stopPropagation(); onNavigate(); }}
          className="ml-0.5 hover:text-[var(--text-primary)]"
          title="Open team detail"
        >
          <ExternalLink className="w-3 h-3" />
        </button>
      )}
    </div>
  );
}
