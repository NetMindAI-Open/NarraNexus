/**
 * @file_name: AgentTeamAvatars.tsx
 * @author: NexusAgent
 * @date: 2026-08-24
 * @description: Shared overlapping Team avatars and hover profiles for Agent surfaces.
 */
import { useTranslation } from 'react-i18next';
import { GroupAvatar } from '@/components/nm';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import type { TeamWithMembers } from '@/types';

interface AgentTeamAvatarsProps {
  agentId: string;
  teams: TeamWithMembers[];
  className?: string;
}

export function AgentTeamAvatars({ agentId, teams, className = '' }: AgentTeamAvatarsProps) {
  const { t } = useTranslation();

  return (
    <span
      data-testid={`teams-${agentId}`}
      className={`flex flex-wrap items-center -space-x-2 pr-2 ${className}`}
      onClick={(event) => event.stopPropagation()}
    >
      {teams.length === 0 && (
        <span className="text-xs text-[var(--nm-ink30)]">—</span>
      )}
      <TooltipProvider delayDuration={180} skipDelayDuration={80}>
        {teams.map((team) => {
          const profile = team.team.description?.trim()
            || team.team.intro_md?.trim()
            || t('pages.dashboard.teamProfileNoDescription');
          return (
            <Tooltip key={team.team.team_id}>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  aria-label={team.team.name}
                  data-testid={`team-avatar-trigger-${team.team.team_id}`}
                  className="relative inline-flex rounded-full outline-none transition-transform hover:z-10 hover:-translate-y-0.5 focus-visible:z-10 focus-visible:ring-2 focus-visible:ring-[var(--nm-ink30)]"
                >
                  <GroupAvatar
                    members={[{ species: 'carbon' }, { species: 'silicon' }]}
                    size="sm"
                    label={teamAvatarLabel(team.team.name)}
                    title={team.team.name}
                  />
                </button>
              </TooltipTrigger>
              <TooltipContent
                side="top"
                align="start"
                className="w-64 p-3"
                style={{
                  background: 'var(--nm-raised)',
                  border: '1px solid var(--nm-hairline)',
                  borderRadius: 'var(--radius-md)',
                  boxShadow: 'var(--nm-elev-2)',
                  color: 'var(--nm-ink)',
                  fontFamily: 'var(--font-sans)',
                }}
              >
                <div className="flex items-center gap-2.5">
                  <GroupAvatar
                    members={[{ species: 'carbon' }, { species: 'silicon' }]}
                    size="md"
                    label={teamAvatarLabel(team.team.name)}
                    title={team.team.name}
                  />
                  <div className="min-w-0">
                    <div className="truncate text-[13px] font-semibold text-[var(--nm-ink)]">
                      {team.team.name}
                    </div>
                    <div className="mt-0.5 text-[10px] text-[var(--nm-ink50)]">
                      {t('pages.dashboard.membersCount', { count: team.member_agent_ids.length })}
                    </div>
                  </div>
                </div>
                <p className="mt-2.5 line-clamp-3 whitespace-pre-line text-[11px] leading-relaxed text-[var(--nm-ink70)]">
                  {profile}
                </p>
              </TooltipContent>
            </Tooltip>
          );
        })}
      </TooltipProvider>
    </span>
  );
}

function teamAvatarLabel(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length > 1) {
    return words.slice(0, 2).map((word) => word.charAt(0)).join('');
  }
  return name.slice(0, 2);
}

export default AgentTeamAvatars;
