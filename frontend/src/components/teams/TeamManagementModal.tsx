/**
 * TeamManagementModal — full CRUD UI for teams (Subproject 1).
 *
 * Layout: list of teams on the left, selected team's details + members on the right.
 * Operations: create/rename/recolor/delete teams; add/remove agents per team; edit intro_md.
 */

import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { createPortal } from 'react-dom';
import { Plus, X, Trash2, Users, Loader2 } from 'lucide-react';
import { useTeamsStore, useConfigStore } from '@/stores';
import { Button, useNotice } from '@/components/ui';
import { cn } from '@/lib/utils';

import { COLOR_PRESETS } from './teamColors';
import { TeamProfileForm } from './TeamProfileForm';

interface Props {
  open: boolean;
  onClose: () => void;
  /** Team to select when the modal opens (e.g. the row whose Manage was
   *  clicked). Only applied on the open transition — switching teams inside
   *  the modal is never overridden. */
  initialTeamId?: string | null;
}

export function TeamManagementModal({ open, onClose, initialTeamId }: Props) {
  const { t } = useTranslation();
  const { teams, refresh, createTeam, updateTeam, deleteTeam, addMember, removeMember, loading } = useTeamsStore();
  const { agents } = useConfigStore();
  // One instance serves the question AND the failure notices: wry does not
  // render window.alert, so every catch block below was silent on the DMG, and
  // none of them has any other feedback (no inline error state).
  const { confirm, notifyError, dialog } = useNotice();

  const [selectedTeamId, setSelectedTeamId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newTeamName, setNewTeamName] = useState('');
  const [newTeamColor, setNewTeamColor] = useState(COLOR_PRESETS[0]);
  const [editLead, setEditLead] = useState('');

  useEffect(() => {
    if (open) refresh();
  }, [open, refresh]);

  // Apply the caller's team on the open transition only; the fallback effect
  // below must not drag the selection back after the user switches manually.
  useEffect(() => {
    if (open && initialTeamId) setSelectedTeamId(initialTeamId);
  }, [open, initialTeamId]);

  useEffect(() => {
    // Fallback only when no caller-directed team exists: if both effects
    // flush in the same commit (selectedTeamId still null), this one runs
    // last and must not overwrite initialTeamId with teams[0].
    if (!selectedTeamId && !initialTeamId && teams.length) {
      setSelectedTeamId(teams[0].team.team_id);
    }
  }, [teams, selectedTeamId, initialTeamId]);

  const selected = useMemo(
    () => teams.find((t) => t.team.team_id === selectedTeamId) || null,
    [teams, selectedTeamId]
  );

  useEffect(() => {
    if (selected) {
      setEditLead(selected.team.lead_agent_id || '');
    }
  }, [selected?.team.team_id, selected?.team.updated_at]);

  if (!open) return null;

  const handleCreate = async () => {
    const name = newTeamName.trim();
    if (!name) return;
    setCreating(true);
    try {
      const tid = await createTeam({ name, color: newTeamColor });
      if (tid) setSelectedTeamId(tid);
      setNewTeamName('');
    } catch (e) {
      void notifyError(t('teams.alert.createFailed', { error: e instanceof Error ? e.message : String(e) }));
    } finally {
      setCreating(false);
    }
  };

  // ONE save for the whole pane: the profile form's live draft plus the
  // lead select's live value, merged here. Two same-named saves that each
  // wrote half (2026-09-03, briefly) reset the other half's draft on the
  // refresh that followed. The lead is read from live state, never from a
  // snapshot taken at open — this dialog is the only editor while it is
  // open, so there is nothing for a snapshot to overwrite, and a live value
  // cannot be stale.
  // What the select SHOWS is also what gets SAVED. The server keeps a lead
  // that was since removed from the team (remove_member does not clear it,
  // and does not bump updated_at, so `editLead` is not re-seeded); the
  // select already falls back to "Auto" for that case, and the save must
  // fall back the same way — otherwise every save of this team would 400
  // with "lead_agent_id must be a team member" until someone touched the
  // select. "" is the backend's clear-to-earliest-member signal.
  const effectiveLead = selected && selected.member_agent_ids.includes(editLead) ? editLead : '';

  const handleSave = async (patch: { name: string; color: string; intro_md: string }) => {
    if (!selected) return;
    try {
      await updateTeam(selected.team.team_id, {
        ...patch,
        lead_agent_id: effectiveLead,
      });
    } catch (e) {
      void notifyError(t('teams.alert.saveFailed', { error: e instanceof Error ? e.message : String(e) }));
    }
  };

  const handleDeleteTeam = async () => {
    if (!selected) return;
    const ok = await confirm({
      title: t('teams.deleteConfirm.title', { name: selected.team.name }),
      message: t('teams.deleteConfirm.message'),
      confirmText: t('teams.deleteConfirm.confirm'),
      danger: true,
    });
    if (!ok) return;
    try {
      const deletedId = selected.team.team_id;
      await deleteTeam(deletedId);
      // Land on the neighbouring team — the one after the deleted row, or
      // the one before it at the end of the list — computed from the
      // pre-delete list (the store hasn't refreshed yet). Jumping to the
      // top of the list would lose the user's place when pruning several
      // teams in a row; writing null would strand the pane on the empty
      // state, since the teams[0] fallback is guarded off whenever the
      // caller passed initialTeamId — which every current caller does.
      const idx = teams.findIndex((tm) => tm.team.team_id === deletedId);
      const neighbour = teams[idx + 1] ?? teams[idx - 1];
      setSelectedTeamId(neighbour?.team.team_id ?? null);
    } catch (e) {
      void notifyError(t('teams.alert.deleteFailed', { error: e instanceof Error ? e.message : String(e) }));
    }
  };

  const handleToggleMember = async (agentId: string) => {
    if (!selected) return;
    // Surface API failures to the user. Pre-fix this handler relied on
    // unhandled-rejection propagation, so a 403 (cross-user agent / team
    // ownership mismatch) or 500 (schema drift, FK violation) silently
    // did nothing — user saw "click Add, nothing happens". Now any
    // backend rejection lands as an alert so they can report a real
    // error string instead of guessing.
    const inTeam = selected.member_agent_ids.includes(agentId);
    try {
      if (inTeam) {
        await removeMember(selected.team.team_id, agentId);
      } else {
        await addMember(selected.team.team_id, agentId);
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      void notifyError(
        inTeam
          ? t('teams.alert.removeFailed', { error: msg })
          : t('teams.alert.addFailed', { error: msg }),
      );
    }
  };

  // Portal to <body>: the sidebar <aside> uses `translate` (mobile drawer
  // slide) which — even at the desktop value of 0px — establishes a
  // containing block for position:fixed descendants, trapping this modal
  // inside the 288px sidebar. Rendering into <body> escapes that subtree so
  // the overlay is viewport-relative and centered.
  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center backdrop-blur-sm"
      style={{ background: 'var(--nm-backdrop)' }}
    >
      <div className="w-[1100px] max-w-[95vw] h-[760px] max-h-[90vh] bg-[var(--bg-primary)] border border-[var(--border-default)] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-[var(--border-default)]">
          <div className="flex items-center gap-2">
            <Users className="w-4 h-4" />
            <h2 className="font-mono text-sm">{t('teams.title')}</h2>
            <span className="text-xs text-[var(--text-tertiary)]">{t('teams.teamCount', { count: teams.length })}</span>
          </div>
          <button onClick={onClose} className="p-1 hover:bg-[var(--nm-paper-warm)]"><X className="w-4 h-4" /></button>
        </div>

        <div className="flex-1 flex overflow-hidden">
          {/* Left: team list + create */}
          <div className="w-[300px] border-r border-[var(--border-default)] flex flex-col">
            <div className="p-3 border-b border-[var(--border-default)] space-y-2">
              <input
                value={newTeamName}
                onChange={(e) => setNewTeamName(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
                placeholder={t('teams.newTeamPlaceholder')}
                className="w-full px-2 py-1.5 text-sm font-mono bg-[var(--bg-tertiary)] border border-[var(--border-default)] focus:outline-none"
              />
              <div className="flex items-center gap-1.5 flex-wrap">
                {COLOR_PRESETS.map((c) => (
                  <button
                    key={c}
                    onClick={() => setNewTeamColor(c)}
                    className={cn(
                      'w-5 h-5 rounded-full border',
                      newTeamColor === c ? 'ring-2 ring-offset-1 ring-[var(--text-primary)]' : ''
                    )}
                    style={{ backgroundColor: c, borderColor: c }}
                  />
                ))}
              </div>
              <Button onClick={handleCreate} disabled={!newTeamName.trim() || creating} size="sm" className="w-full gap-1">
                {creating ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
                {t('teams.createTeam')}
              </Button>
            </div>
            <div className="flex-1 overflow-y-auto">
              {loading && <div className="p-4 text-xs text-[var(--text-tertiary)]">{t('teams.loading')}</div>}
              {!loading && teams.length === 0 && (
                <div className="p-4 text-xs text-[var(--text-tertiary)]">{t('teams.emptyList')}</div>
              )}
              {teams.map((tm) => (
                <button
                  key={tm.team.team_id}
                  onClick={() => setSelectedTeamId(tm.team.team_id)}
                  className={cn(
                    'w-full text-left px-3 py-2 border-b border-[var(--border-subtle)] hover:bg-[var(--nm-paper-warm)] flex items-center gap-2',
                    selectedTeamId === tm.team.team_id && 'bg-[var(--bg-elevated)]'
                  )}
                >
                  <span
                    className="w-2.5 h-2.5 rounded-full shrink-0"
                    style={{ backgroundColor: tm.team.color || '#666' }}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-mono truncate">{tm.team.name}</div>
                    <div className="text-[10px] text-[var(--text-tertiary)]">{t('teams.memberCount', { count: tm.member_agent_ids.length })}</div>
                  </div>
                  {tm.team.source === 'bundle' && (
                    <span className="text-[9px] uppercase border border-[var(--border-subtle)] px-1 py-px text-[var(--text-tertiary)]">{t('teams.imported')}</span>
                  )}
                </button>
              ))}
            </div>
          </div>

          {/* Right: details */}
          <div className="flex-1 overflow-y-auto">
            {!selected ? (
              <div className="h-full flex items-center justify-center text-sm text-[var(--text-tertiary)]">
                {t('teams.selectPrompt')}
              </div>
            ) : (
              <div className="p-5 space-y-5">
                <TeamProfileForm
                  team={selected.team}
                  onSave={handleSave}
                  trailing={
                    <Button onClick={handleDeleteTeam} variant="ghost" size="sm" className="gap-1 text-[var(--color-error)]">
                      <Trash2 className="w-3.5 h-3.5" />
                      {t('teams.deleteTeam')}
                    </Button>
                  }
                >
                  {/* Team lead — the agent that answers a team message
                      with no @mention. "Auto" = the earliest-joined member. */}
                  <div className="space-y-2 pt-3 border-t border-[var(--border-default)]">
                    <label className="text-xs uppercase text-[var(--text-tertiary)]">{t('teams.leadLabel')}</label>
                    <select
                      value={effectiveLead}
                      onChange={(e) => setEditLead(e.target.value)}
                      className="w-full px-3 py-2 text-sm bg-[var(--bg-tertiary)] border border-[var(--border-default)] focus:outline-none"
                    >
                      <option value="">{t('teams.leadAuto')}</option>
                      {selected.member_agent_ids.map((mid) => (
                        <option key={mid} value={mid}>
                          {agents.find((a) => a.agent_id === mid)?.name || mid}
                        </option>
                      ))}
                    </select>
                    <p className="text-[10px] text-[var(--text-tertiary)]">{t('teams.leadHint')}</p>
                  </div>
                </TeamProfileForm>

                {/* Members */}
                <div className="space-y-2 pt-3 border-t border-[var(--border-default)]">
                  <label className="text-xs uppercase text-[var(--text-tertiary)]">{t('teams.membersLabel', { selected: selected.member_agent_ids.length, total: agents.length })}</label>
                  <div className="border border-[var(--border-default)] divide-y divide-[var(--border-subtle)] max-h-[280px] overflow-y-auto">
                    {agents.length === 0 && (
                      <div className="p-3 text-xs text-[var(--text-tertiary)]">{t('teams.noAgents')}</div>
                    )}
                    {agents.map((a) => {
                      const inTeam = selected.member_agent_ids.includes(a.agent_id);
                      return (
                        <div key={a.agent_id} className="flex items-center justify-between px-3 py-2 hover:bg-[var(--nm-paper-warm)]">
                          <div className="flex-1 min-w-0">
                            <div className="text-sm font-mono truncate">{a.name || a.agent_id}</div>
                            <div className="text-[10px] text-[var(--text-tertiary)] truncate">{a.agent_id}</div>
                          </div>
                          <button
                            onClick={() => handleToggleMember(a.agent_id)}
                            className={cn(
                              'text-xs px-2 py-1 border',
                              inTeam
                                ? 'border-[var(--color-error)] text-[var(--color-error)] hover:bg-[var(--color-error)]/10'
                                : 'border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--nm-paper-warm)]'
                            )}
                          >
                            {inTeam ? t('teams.remove') : t('teams.add')}
                          </button>
                        </div>
                      );
                    })}
                  </div>
                </div>

              </div>
            )}
          </div>
        </div>
        {dialog}
      </div>
    </div>,
    document.body,
  );
}
