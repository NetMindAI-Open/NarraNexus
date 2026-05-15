/**
 * ManageAgentsPage — batch operations on agents.
 *
 * Subproject 1 + 2 follow-up: 议题 8 simplification (no "undo import" feature)
 * makes a batch-management surface table-stakes for users who imported a
 * bundle and want to clean up.
 *
 * Features:
 * - Multi-select with shift-click range selection
 * - Filter: by team membership, by source ('bundle' imports), by name
 * - Bulk delete (with cascading via existing /api/agents/:id DELETE)
 * - Bulk add to team / remove from team
 */

import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ArrowLeft, Trash2, UserCheck, UserMinus, Loader2, Search, Filter,
  CheckSquare, Square, AlertTriangle,
} from 'lucide-react';
import { Button, ScrollArea, useConfirm } from '@/components/ui';
import { useConfigStore, useTeamsStore } from '@/stores';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';

export default function ManageAgentsPage() {
  const navigate = useNavigate();
  const { agents, userId, refreshAgents } = useConfigStore();
  const { teams, refresh: refreshTeams, addMember, removeMember } = useTeamsStore();
  const { confirm, alert, dialog } = useConfirm();

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [lastClickedIdx, setLastClickedIdx] = useState<number | null>(null);
  const [filterTeam, setFilterTeam] = useState<string>('');   // '' / 'untagged' / 'imported' / <team_id>
  const [filterText, setFilterText] = useState('');
  const [busy, setBusy] = useState(false);
  const [bulkTeamPicker, setBulkTeamPicker] = useState<string>('');

  useEffect(() => { refreshTeams(); refreshAgents(); }, [refreshTeams, refreshAgents]);

  // Derive imported-agent set: agents that are members of any team with source='bundle'
  const importedAgentIds = useMemo(() => {
    const s = new Set<string>();
    teams.forEach((t) => {
      if (t.team.source === 'bundle') {
        t.member_agent_ids.forEach((id) => s.add(id));
      }
    });
    return s;
  }, [teams]);

  const filteredAgents = useMemo(() => {
    let list = [...agents];
    if (filterText) {
      const q = filterText.toLowerCase();
      list = list.filter((a) =>
        (a.name || '').toLowerCase().includes(q) || a.agent_id.toLowerCase().includes(q)
      );
    }
    if (filterTeam === 'untagged') {
      const taggedIds = new Set<string>();
      teams.forEach((t) => t.member_agent_ids.forEach((id) => taggedIds.add(id)));
      list = list.filter((a) => !taggedIds.has(a.agent_id));
    } else if (filterTeam === 'imported') {
      list = list.filter((a) => importedAgentIds.has(a.agent_id));
    } else if (filterTeam) {
      const team = teams.find((t) => t.team.team_id === filterTeam);
      const memberIds = new Set(team?.member_agent_ids || []);
      list = list.filter((a) => memberIds.has(a.agent_id));
    }
    return list;
  }, [agents, filterText, filterTeam, teams, importedAgentIds]);

  const allSelected = filteredAgents.length > 0
    && filteredAgents.every((a) => selected.has(a.agent_id));

  const toggleAll = () => {
    setSelected((prev) => {
      if (allSelected) {
        const next = new Set(prev);
        filteredAgents.forEach((a) => next.delete(a.agent_id));
        return next;
      }
      const next = new Set(prev);
      filteredAgents.forEach((a) => next.add(a.agent_id));
      return next;
    });
  };

  const toggleAgent = (agentId: string, idx: number, ev: React.MouseEvent) => {
    if (ev.shiftKey && lastClickedIdx !== null) {
      const [a, b] = [Math.min(idx, lastClickedIdx), Math.max(idx, lastClickedIdx)];
      const target = !selected.has(agentId);
      setSelected((prev) => {
        const next = new Set(prev);
        for (let i = a; i <= b; i++) {
          const id = filteredAgents[i].agent_id;
          if (target) next.add(id); else next.delete(id);
        }
        return next;
      });
    } else {
      setSelected((prev) => {
        const next = new Set(prev);
        if (next.has(agentId)) next.delete(agentId);
        else next.add(agentId);
        return next;
      });
    }
    setLastClickedIdx(idx);
  };

  const handleBulkDelete = async () => {
    if (selected.size === 0) return;
    const ok = await confirm({
      title: `Delete ${selected.size} agent${selected.size === 1 ? '' : 's'}?`,
      message:
        'This will permanently delete the selected agents AND their narratives, ' +
        'events, instances, social entities, workspace files. This cannot be undone.',
      confirmText: `Delete ${selected.size}`,
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    let success = 0, failed: string[] = [];
    for (const aid of Array.from(selected)) {
      try {
        await api.deleteAgent(aid, userId);
        success += 1;
      } catch (e: any) {
        failed.push(aid);
      }
    }
    await refreshAgents();
    await refreshTeams();
    setSelected(new Set());
    setBusy(false);
    await alert({
      title: 'Bulk delete complete',
      message: `${success} deleted${failed.length ? `, ${failed.length} failed (${failed.slice(0, 3).join(', ')}${failed.length > 3 ? '…' : ''})` : ''}.`,
      danger: failed.length > 0,
    });
  };

  const handleBulkAddToTeam = async () => {
    if (selected.size === 0 || !bulkTeamPicker) return;
    setBusy(true);
    for (const aid of Array.from(selected)) {
      try { await addMember(bulkTeamPicker, aid); } catch {}
    }
    await refreshTeams();
    setBusy(false);
    await alert({
      title: 'Added to team',
      message: `${selected.size} agent${selected.size === 1 ? '' : 's'} added to selected team.`,
    });
  };

  const handleBulkRemoveFromTeam = async () => {
    if (selected.size === 0 || !bulkTeamPicker) return;
    setBusy(true);
    for (const aid of Array.from(selected)) {
      try { await removeMember(bulkTeamPicker, aid); } catch {}
    }
    await refreshTeams();
    setBusy(false);
  };

  const teamLookupForAgent = (aid: string) =>
    teams.filter((t) => t.member_agent_ids.includes(aid)).map((t) => t.team);

  return (
    <ScrollArea className="h-full" viewportClassName="px-6 py-5">
      <div className="max-w-5xl mx-auto space-y-4">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button onClick={() => navigate('/app/settings')} className="p-1 hover:bg-[var(--bg-tertiary)]">
              <ArrowLeft className="w-4 h-4" />
            </button>
            <h1 className="font-mono text-base">Manage agents (batch)</h1>
            <span className="text-xs text-[var(--text-tertiary)]">
              {filteredAgents.length} shown · {selected.size} selected · {agents.length} total
            </span>
          </div>
        </div>

        {/* Filter bar */}
        <div className="flex items-center gap-2 border border-[var(--border-subtle)] p-2 bg-[var(--bg-secondary)]">
          <Search className="w-3.5 h-3.5 text-[var(--text-tertiary)]" />
          <input
            value={filterText}
            onChange={(e) => setFilterText(e.target.value)}
            placeholder="Search by name / id…"
            className="flex-1 bg-transparent text-sm font-mono focus:outline-none"
          />
          <Filter className="w-3.5 h-3.5 text-[var(--text-tertiary)] ml-2" />
          <select
            value={filterTeam}
            onChange={(e) => setFilterTeam(e.target.value)}
            className="bg-[var(--bg-tertiary)] text-xs font-mono px-2 py-1 border border-[var(--border-subtle)]"
          >
            <option value="">All agents</option>
            <option value="untagged">Untagged</option>
            <option value="imported">From bundles</option>
            <optgroup label="By team">
              {teams.map((t) => (
                <option key={t.team.team_id} value={t.team.team_id}>{t.team.name}</option>
              ))}
            </optgroup>
          </select>
        </div>

        {/* Bulk actions bar */}
        <div className={cn(
          'flex items-center gap-2 border p-2',
          selected.size > 0
            ? 'border-[var(--border-strong)] bg-[var(--bg-elevated)]'
            : 'border-[var(--border-subtle)] bg-[var(--bg-secondary)] opacity-60'
        )}>
          <button onClick={toggleAll} className="flex items-center gap-1 text-xs font-mono">
            {allSelected ? <CheckSquare className="w-3.5 h-3.5" /> : <Square className="w-3.5 h-3.5" />}
            {allSelected ? 'Unselect all' : 'Select all (shown)'}
          </button>
          <span className="flex-1" />
          <select
            value={bulkTeamPicker}
            onChange={(e) => setBulkTeamPicker(e.target.value)}
            disabled={selected.size === 0}
            className="bg-[var(--bg-tertiary)] text-xs font-mono px-2 py-1 border border-[var(--border-subtle)]"
          >
            <option value="">— pick team —</option>
            {teams.map((t) => (
              <option key={t.team.team_id} value={t.team.team_id}>{t.team.name}</option>
            ))}
          </select>
          <Button
            onClick={handleBulkAddToTeam}
            disabled={selected.size === 0 || !bulkTeamPicker || busy}
            size="sm"
            variant="outline"
            className="gap-1"
          >
            <UserCheck className="w-3.5 h-3.5" /> Add to team
          </Button>
          <Button
            onClick={handleBulkRemoveFromTeam}
            disabled={selected.size === 0 || !bulkTeamPicker || busy}
            size="sm"
            variant="outline"
            className="gap-1"
          >
            <UserMinus className="w-3.5 h-3.5" /> Remove from team
          </Button>
          <Button
            onClick={handleBulkDelete}
            disabled={selected.size === 0 || busy}
            size="sm"
            className="gap-1 bg-[var(--color-red-500)]/15 border border-[var(--color-red-500)] text-[var(--color-red-500)] hover:bg-[var(--color-red-500)]/30"
          >
            {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
            Delete
          </Button>
        </div>

        {/* Helper text for "imported" filter */}
        {filterTeam === 'imported' && (
          <div className="text-[11px] text-[var(--text-tertiary)] flex items-center gap-1.5">
            <AlertTriangle className="w-3 h-3 text-[var(--color-yellow-500)]" />
            Showing agents that came from a `.nxbundle` import (member of a team with source=bundle).
            This is the closest thing to "undo import" — select all and Delete.
          </div>
        )}

        {/* Agent table */}
        <div className="border border-[var(--border-default)]">
          <div className="grid grid-cols-[24px_1fr_200px_140px] gap-3 px-3 py-2 text-[10px] uppercase tracking-widest text-[var(--text-tertiary)] border-b border-[var(--border-default)] bg-[var(--bg-secondary)]">
            <span></span>
            <span>Name / ID</span>
            <span>Teams</span>
            <span>Source</span>
          </div>
          {filteredAgents.length === 0 ? (
            <div className="px-4 py-8 text-center text-xs text-[var(--text-tertiary)]">
              No agents match the current filter.
            </div>
          ) : (
            filteredAgents.map((a, idx) => {
              const isSel = selected.has(a.agent_id);
              const aTeams = teamLookupForAgent(a.agent_id);
              const isImported = importedAgentIds.has(a.agent_id);
              return (
                <div
                  key={a.agent_id}
                  className={cn(
                    'grid grid-cols-[24px_1fr_200px_140px] gap-3 px-3 py-2 text-sm border-b border-[var(--border-subtle)] cursor-pointer items-center',
                    isSel ? 'bg-[var(--bg-elevated)]' : 'hover:bg-[var(--bg-tertiary)]'
                  )}
                  onClick={(e) => toggleAgent(a.agent_id, idx, e)}
                >
                  <span>
                    {isSel
                      ? <CheckSquare className="w-3.5 h-3.5 text-[var(--text-primary)]" />
                      : <Square className="w-3.5 h-3.5 text-[var(--text-tertiary)]" />}
                  </span>
                  <div className="min-w-0">
                    <div className="font-mono truncate">{a.name || a.agent_id}</div>
                    <div className="text-[10px] text-[var(--text-tertiary)] truncate">{a.agent_id}</div>
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {aTeams.length === 0 && (
                      <span className="text-[10px] text-[var(--text-tertiary)] italic">untagged</span>
                    )}
                    {aTeams.map((t) => (
                      <span
                        key={t.team_id}
                        className="text-[10px] font-mono px-1.5 py-0.5 border"
                        style={{
                          borderColor: t.color || 'var(--border-subtle)',
                          color: t.color || 'var(--text-secondary)',
                        }}
                      >
                        {t.name}
                      </span>
                    ))}
                  </div>
                  <div className="text-[10px] text-[var(--text-tertiary)]">
                    {isImported ? (
                      <span className="text-[var(--color-yellow-500)]">from bundle</span>
                    ) : (
                      'created locally'
                    )}
                  </div>
                </div>
              );
            })
          )}
        </div>

        <p className="text-[11px] text-[var(--text-tertiary)] pt-2">
          Tip: shift-click to select a range. Bulk delete cascades through narratives,
          events, module instances, social entities, and the agent workspace dir.
        </p>
        {dialog}
      </div>
    </ScrollArea>
  );
}
