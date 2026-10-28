/**
 * @file_name: TeamManagePanel.tsx
 * @author: NarraNexus
 * @date: 2026-09-03
 * @description: The team room's "Team management" drawer tab — the one place
 * the owner manages a team from inside its room.
 *
 * Before this panel the same controls were scattered over four surfaces:
 * the bulletin behind a small button at the far end of the header, the
 * patrol switch at the bottom of the work board, lead + members inside the
 * edit modal behind the settings page, and "clear data" in the sidebar row's
 * ⋮ menu. The profile (name / colour / intro) is the shared TeamProfileForm
 * rendered inline — the team manager modal is NOT mounted here (it switches
 * and creates teams, and its delete would not leave this room), so lead,
 * members and delete each have exactly one live editor: this panel. The
 * 2026-09-03 feedback was literally "I cannot find where to
 * write the bulletin". So: one tab, sections top to bottom in the order a
 * team is usually managed — what the team must know (bulletin), who answers
 * (lead), whether the lead chases (patrol), who is in it (members), and the
 * destructive tail (clear / delete) last.
 *
 * The panel OWNS nothing the room also needs. The bulletin state stays in
 * TeamChatPanel (a change posts a system line, and the transcript and this
 * panel must agree on when that happened); the lead comes from the room's
 * poll; members come from the teams store. This panel only renders and
 * calls back.
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { Crown, Eraser, Loader2, Pencil, Trash2, Users } from 'lucide-react';
import { Button, useNotice } from '@/components/ui';
import { useTeamsStore } from '@/stores';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';
import { TeamBulletinPanel } from './TeamBulletinPanel';
import { TeamProfileForm } from '@/components/teams/TeamProfileForm';
import { ClearTeamDataDialog } from '@/components/teams/ClearTeamDataDialog';
import type { AgentInfo } from '@/types';
import type { TeamBulletin } from '@/types/teams';

export interface TeamManagePanelProps {
  teamId: string;
  teamName: string;
  /** The team row — what the profile form edits. */
  team: { team_id: string; name: string; color?: string | null; intro_md?: string | null; updated_at?: string | null };
  /** The team's current members, in membership order. */
  members: AgentInfo[];
  /** Every agent the account owns — the pool members are added from. */
  allAgents: AgentInfo[];
  leadAgentId: string | null;
  onSetLead: (agentId: string) => void;
  /** Bulletin — owned by the room, rendered here. */
  bulletin: TeamBulletin | null;
  bulletinLoading: boolean;
  bulletinError: string | null;
  memberNames: Record<string, string>;
  onBulletinAdd: (content: string, tier: 'long_term' | 'current_task') => Promise<string | null>;
  onBulletinEdit: (entryId: string, content: string) => Promise<string | null>;
  onBulletinDelete: (entryId: string) => Promise<string | null>;
  onBulletinClearTier: (tier: 'long_term' | 'current_task') => Promise<string | null>;
  /** Fired after a clear so the room can drop stale transcript/workspace. */
  onCleared: (scopes: { chat: boolean; files: boolean; bulletin: boolean }) => void;
  className?: string;
}

export function TeamManagePanel({
  teamId,
  teamName,
  team,
  members,
  allAgents,
  leadAgentId,
  onSetLead,
  bulletin,
  bulletinLoading,
  bulletinError,
  memberNames,
  onBulletinAdd,
  onBulletinEdit,
  onBulletinDelete,
  onBulletinClearTier,
  onCleared,
  className,
}: TeamManagePanelProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { confirm, notifyError, dialog } = useNotice();
  // Selector form on purpose: the room's tests mock the store as a selector
  // hook, and the destructured form the modal uses would not survive that.
  const addMember = useTeamsStore((s) => s.addMember);
  const removeMember = useTeamsStore((s) => s.removeMember);
  const deleteTeam = useTeamsStore((s) => s.deleteTeam);
  const updateTeam = useTeamsStore((s) => s.updateTeam);

  // Patrol — ONE copy, in the teams store. The room's 3s poll (and the work
  // board's, when that tab is open) keep it current; this panel only reads
  // it and writes through the store, so a flip made elsewhere shows here
  // before the user clicks. `undefined` = not reported yet.
  const patrolEnabled = useTeamsStore((s) => s.patrolByTeam[teamId]);
  const patrolInFlight = useTeamsStore((s) => s.patrolInFlight[teamId] === true);
  const setPatrol = useTeamsStore((s) => s.setPatrol);
  const togglePatrol = async () => {
    if (patrolEnabled === undefined || patrolInFlight) return;
    try {
      await setPatrol(teamId, !patrolEnabled);
    } catch {
      // the store already rolled the optimistic flip back
    }
  };

  const [busyAgent, setBusyAgent] = useState<string | null>(null);
  const toggleMember = async (agentId: string) => {
    const inTeam = members.some((m) => m.agent_id === agentId);
    setBusyAgent(agentId);
    try {
      if (inTeam) await removeMember(teamId, agentId);
      else await addMember(teamId, agentId);
    } catch (e) {
      void notifyError(
        t('teams.alert.saveFailed', { error: e instanceof Error ? e.message : String(e) }),
      );
    } finally {
      setBusyAgent(null);
    }
  };

  const saveProfile = async (patch: { name: string; color: string; intro_md: string }) => {
    try {
      await updateTeam(teamId, patch);
    } catch (e) {
      void notifyError(
        t('teams.alert.saveFailed', { error: e instanceof Error ? e.message : String(e) }),
      );
    }
  };

  const [clearing, setClearing] = useState(false);
  const [clearBusy, setClearBusy] = useState(false);

  const doClear = async (scopes: { chat: boolean; files: boolean; bulletin: boolean }) => {
    setClearBusy(true);
    try {
      const res = await api.clearTeamData(teamId, scopes);
      if (!res.success) {
        void notifyError(res.error || 'Failed to clear team data');
        return;
      }
      onCleared(scopes);
    } catch (e) {
      void notifyError(e instanceof Error ? e.message : String(e));
    } finally {
      setClearBusy(false);
      setClearing(false);
    }
  };

  const doDelete = async () => {
    const ok = await confirm({
      title: t('teams.deleteConfirm.title', { name: teamName }),
      message: t('teams.deleteConfirm.message'),
      confirmText: t('teams.deleteConfirm.confirm'),
      danger: true,
    });
    if (!ok) return;
    try {
      await deleteTeam(teamId);
      // The room is gone; the nearest place that still exists is the chat.
      navigate('/app/chat');
    } catch (e) {
      void notifyError(
        t('teams.alert.deleteFailed', { error: e instanceof Error ? e.message : String(e) }),
      );
    }
  };

  const sectionLabel = 'font-mono text-[10px] uppercase tracking-[0.18em] text-[var(--text-tertiary)]';

  return (
    <div
      className={cn('flex min-h-0 flex-col overflow-y-auto', className)}
      data-testid="team-manage-panel"
    >
      {/* 1. Bulletin — the reason this tab exists. */}
      <section className="border-b border-[var(--border-subtle)]">
        <div className="px-3 pt-3">
          <h4 className={sectionLabel}>{t('chat.team.bulletin.title')}</h4>
        </div>
        <TeamBulletinPanel
          bulletin={bulletin}
          loading={bulletinLoading}
          error={bulletinError}
          memberNames={memberNames}
          onAdd={onBulletinAdd}
          onEdit={onBulletinEdit}
          onDelete={onBulletinDelete}
          onClearTier={onBulletinClearTier}
        />
      </section>

      {/* 2. Lead — who answers a message that names nobody. */}
      <section className="space-y-1.5 border-b border-[var(--border-subtle)] px-3 py-3">
        <h4 className={cn(sectionLabel, 'flex items-center gap-1.5')}>
          <Crown className="h-3 w-3" /> {t('teams.leadLabel')}
        </h4>
        <select
          data-testid="manage-lead-select"
          value={leadAgentId && members.some((m) => m.agent_id === leadAgentId) ? leadAgentId : ''}
          onChange={(e) => onSetLead(e.target.value)}
          className="w-full rounded-[var(--radius-xs)] border border-[var(--border-subtle)] bg-transparent px-2 py-1.5 text-sm text-[var(--text-primary)] focus:outline-none"
        >
          <option value="">{t('teams.leadAuto')}</option>
          {members.map((m) => (
            <option key={m.agent_id} value={m.agent_id}>
              {m.name || m.agent_id}
            </option>
          ))}
        </select>
        <p className="text-[10px] text-[var(--text-tertiary)]">{t('teams.leadHint')}</p>
      </section>

      {/* 3. Patrol — whether the lead chases stalled hand-offs. */}
      <section className="border-b border-[var(--border-subtle)] px-3 py-3">
        <div className="flex items-center justify-between gap-2">
          <div className="min-w-0">
            <h4 className={sectionLabel}>{t('chat.team.manage.patrol')}</h4>
            <p className="mt-1 text-[10px] text-[var(--text-tertiary)]">
              {t('chat.team.manage.patrolHint')}
            </p>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={patrolEnabled === true}
            data-testid="patrol-toggle"
            // Disabled while unknown AND while a write is in flight — but not
            // during the settle window after it, or the switch would feel stuck.
            disabled={patrolEnabled === undefined || patrolInFlight}
            onClick={togglePatrol}
            className={cn(
              'shrink-0 rounded-[var(--radius-xs)] border px-2 py-1 text-[11px] transition-colors',
              patrolEnabled
                ? 'border-[var(--color-success)] text-[var(--color-success)]'
                : 'border-[var(--border-subtle)] text-[var(--text-secondary)]',
              (patrolEnabled === undefined || patrolInFlight) && 'opacity-50',
            )}
          >
            {patrolEnabled ? t('chat.team.manage.patrolOff') : t('chat.team.manage.patrolOn')}
          </button>
        </div>
      </section>

      {/* 4. Members — the account's agents, in or out. */}
      <section className="space-y-1.5 border-b border-[var(--border-subtle)] px-3 py-3">
        <h4 className={cn(sectionLabel, 'flex items-center gap-1.5')}>
          <Users className="h-3 w-3" />{' '}
          {t('teams.membersLabel', { selected: members.length, total: allAgents.length })}
        </h4>
        <div className="max-h-[240px] divide-y divide-[var(--border-subtle)] overflow-y-auto rounded-[var(--radius-xs)] border border-[var(--border-subtle)]">
          {allAgents.length === 0 && (
            <div className="p-2 text-xs text-[var(--text-tertiary)]">{t('teams.noAgents')}</div>
          )}
          {allAgents.map((a) => {
            const inTeam = members.some((m) => m.agent_id === a.agent_id);
            return (
              <div
                key={a.agent_id}
                className="flex items-center justify-between gap-2 px-2 py-1.5 hover:bg-[var(--nm-paper-warm)]"
              >
                <div className="min-w-0 flex-1">
                  <div className="truncate text-xs text-[var(--text-primary)]">
                    {a.name || a.agent_id}
                  </div>
                </div>
                <button
                  type="button"
                  data-testid={`manage-member-${a.agent_id}`}
                  disabled={busyAgent === a.agent_id}
                  onClick={() => toggleMember(a.agent_id)}
                  className={cn(
                    'shrink-0 rounded-[var(--radius-xs)] border px-1.5 py-0.5 text-[10px]',
                    inTeam
                      ? 'border-[var(--danger)]/60 text-[var(--danger)] hover:bg-[var(--danger)]/10'
                      : 'border-[var(--border-subtle)] text-[var(--text-secondary)] hover:bg-[var(--nm-paper-warm)]',
                  )}
                >
                  {busyAgent === a.agent_id ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : inTeam ? (
                    t('teams.remove')
                  ) : (
                    t('teams.add')
                  )}
                </button>
              </div>
            );
          })}
        </div>
      </section>

      {/* 5. Profile — the same form the team manager modal uses, inline.
          Not the modal: that one switches teams, creates teams, and its
          delete does not leave this room. */}
      <section className="space-y-1.5 border-b border-[var(--border-subtle)] px-3 py-3">
        <h4 className={cn(sectionLabel, 'flex items-center gap-1.5')}>
          <Pencil className="h-3 w-3" /> {t('chat.team.manage.editProfile')}
        </h4>
        <TeamProfileForm team={team} onSave={saveProfile} />
      </section>

      {/* 6–7. The destructive tail. */}
      <section className="flex flex-col gap-1.5 px-3 py-3">
        <Button
          variant="outline"
          size="sm"
          className="justify-start gap-1.5"
          data-testid="manage-clear-data"
          onClick={() => setClearing(true)}
        >
          <Eraser className="h-3.5 w-3.5" /> {t('chat.team.manage.clearData')}
        </Button>
        <Button
          variant="danger"
          size="sm"
          className="justify-start gap-1.5"
          data-testid="manage-delete-team"
          onClick={doDelete}
        >
          <Trash2 className="h-3.5 w-3.5" /> {t('teams.deleteTeam')}
        </Button>
      </section>

      {clearing && (
        <ClearTeamDataDialog
          teamName={teamName}
          busy={clearBusy}
          onCancel={() => setClearing(false)}
          onConfirm={doClear}
        />
      )}
      {dialog}
    </div>
  );
}
