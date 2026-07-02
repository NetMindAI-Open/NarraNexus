/**
 * TeamDetailPage — full team-detail view with markdown intro and member roster.
 *
 * Subproject 1 + 2 (议题 8 onboarding):
 * - Renders teams.intro_md (set by TeamManagementModal OR seeded from a
 *   bundle's README.md on import).
 * - Lists member agents with click-to-select (sets agentId in chat).
 * - Provides "Edit team" shortcut that opens TeamManagementModal.
 */

import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Pencil, Users, Bot, Package } from 'lucide-react';
import { Button, ScrollArea } from '@/components/ui';
import { BracketEmptyState, BracketSectionLabel } from '@/components/nm';
import { Markdown } from '@/components/ui/Markdown';
import { TeamManagementModal } from '@/components/teams/TeamManagementModal';
import { useTeamsStore, useConfigStore } from '@/stores';

export default function TeamDetailPage() {
  const { teamId } = useParams<{ teamId: string }>();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { teams, refresh } = useTeamsStore();
  const { agents, setAgentId } = useConfigStore();
  const [editing, setEditing] = useState(false);

  useEffect(() => { refresh(); }, [refresh]);

  const team = useMemo(
    () => teams.find((t) => t.team.team_id === teamId) || null,
    [teams, teamId]
  );

  if (!team) {
    return (
      <div className="h-full flex items-center justify-center text-sm text-[var(--text-tertiary)]">
        {teams.length === 0 ? t('pages.teamDetail.loading') : t('pages.teamDetail.notFound')}
      </div>
    );
  }

  const memberAgents = agents.filter((a) =>
    team.member_agent_ids.includes(a.agent_id)
  );

  return (
    <ScrollArea className="h-full" viewportClassName="px-6 py-5">
      <div className="max-w-3xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex items-start gap-3">
          <button
            onClick={() => navigate(-1)}
            className="p-1 mt-1 hover:bg-[var(--bg-tertiary)]"
            title={t('pages.teamDetail.back')}
          >
            <ArrowLeft className="w-4 h-4" />
          </button>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span
                className="w-3 h-3 rounded-full shrink-0"
                style={{ backgroundColor: team.team.color || '#666' }}
              />
              <h1 className="text-xl font-mono truncate">{team.team.name}</h1>
              {team.team.source === 'bundle' && (
                <span className="text-[10px] uppercase border border-[var(--border-subtle)] px-1.5 py-0.5 text-[var(--text-tertiary)]">
                  {t('pages.teamDetail.imported')}
                </span>
              )}
            </div>
            <div className="text-xs text-[var(--text-tertiary)] mt-1">
              {t('pages.teamDetail.memberCount', { count: team.member_agent_ids.length })}
              {team.team.description ? ` · ${team.team.description}` : ''}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button
              onClick={() => {
                // Pre-seed Export wizard with this team's agents preselected
                // by passing the team_id through the URL. BundleExportPage
                // reads ?team=<id>&agents=<csv> on mount and primes its
                // selectedTeam + selectedAgents accordingly.
                const memberCsv = team.member_agent_ids.join(',');
                navigate(
                  `/app/bundle/export?team=${encodeURIComponent(team.team.team_id)}` +
                  `&agents=${encodeURIComponent(memberCsv)}`
                );
              }}
              variant="outline"
              size="sm"
              className="gap-1"
              disabled={team.member_agent_ids.length === 0}
              title={
                team.member_agent_ids.length === 0
                  ? t('pages.teamDetail.exportDisabledTitle')
                  : t('pages.teamDetail.exportTitle')
              }
            >
              <Package className="w-3.5 h-3.5" />
              {t('pages.teamDetail.exportTeam')}
            </Button>
            <Button
              onClick={() => setEditing(true)}
              variant="outline"
              size="sm"
              className="gap-1"
            >
              <Pencil className="w-3.5 h-3.5" />
              {t('pages.teamDetail.edit')}
            </Button>
          </div>
        </div>

        {/* Intro markdown */}
        {team.team.intro_md ? (
          <section className="rounded-[var(--radius-md)] border border-[color:var(--nm-hairline)] bg-[color:var(--nm-card)] p-5">
            <BracketSectionLabel>{t('pages.teamDetail.teamIntro')}</BracketSectionLabel>
            <div className="mt-3">
              <Markdown content={team.team.intro_md} />
            </div>
          </section>
        ) : (
          <BracketEmptyState
            label={t('pages.teamDetail.noIntroLabel')}
            hint={t('pages.teamDetail.noIntroHint')}
          />
        )}

        {/* Member roster */}
        <section>
          <BracketSectionLabel>
            <span className="inline-flex items-center gap-2"><Users className="w-3 h-3" /> {t('pages.teamDetail.members')}</span>
          </BracketSectionLabel>
          {memberAgents.length === 0 ? (
            <div className="mt-3">
              <BracketEmptyState
                label={t('pages.teamDetail.noMembersLabel')}
                hint={t('pages.teamDetail.noMembersHint')}
              />
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-2">
              {memberAgents.map((a) => (
                <button
                  key={a.agent_id}
                  onClick={() => {
                    setAgentId(a.agent_id);
                    navigate('/app/chat');
                  }}
                  className="text-left p-3 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)] flex items-start gap-2"
                >
                  <Bot className="w-4 h-4 mt-0.5 text-[var(--text-secondary)]" />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-mono truncate">{a.name || a.agent_id}</div>
                    <div className="text-[10px] text-[var(--text-tertiary)] truncate">{a.agent_id}</div>
                  </div>
                </button>
              ))}
            </div>
          )}
        </section>
      </div>
      <TeamManagementModal open={editing} onClose={() => setEditing(false)} />
    </ScrollArea>
  );
}
