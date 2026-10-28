/**
 * @file_name: TeamMemberAvatars.tsx
 * @author: NexusAgent
 * @date: 2026-08-25
 * @description: Overlapping per-agent avatars for a Team's member list — the
 * agent-list-style counterpart to AgentTeamAvatars (which shows Team avatars
 * on an Agent row; this shows Agent avatars on a Team row). Each avatar opens
 * a hover profile card summarising who that member is (status, runtime,
 * model, owner), and navigates to that agent's Profile page on click.
 *
 * The click target matches the Agents table's identity block exactly — same
 * route, same `from: 'dashboard'` origin tag — so Profile's back button
 * behaves the same no matter which of the two tables sent the user there.
 */
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Lock, Globe } from 'lucide-react';
import { RingAvatar, StatusDot, type NMStatusKind } from '@/components/nm';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { formatFrameworkFromList } from '@/lib/frameworkBrand';
import type { AgentInfo, AgentStatus } from '@/types';

interface TeamMemberAvatarsProps {
  memberAgentIds: string[];
  agentsById: Map<string, AgentInfo>;
  statusById: Map<string, AgentStatus>;
  currentUserId?: string;
  currentUserDisplayName?: string;
  max?: number;
  className?: string;
}

export function TeamMemberAvatars({
  memberAgentIds,
  agentsById,
  statusById,
  currentUserId,
  currentUserDisplayName,
  max = 4,
  className = '',
}: TeamMemberAvatarsProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();

  const statusCellOf = (status: AgentStatus | undefined): { label: string; kind: NMStatusKind } => {
    if (!status) return { label: '—', kind: 'neutral' };
    if (status.owned_by_viewer) {
      const health = status.health;
      if (health === 'error') return { label: t('dashboard.summary.chip.error'), kind: 'error' };
      if (health === 'warning') return { label: t('dashboard.summary.chip.blocked'), kind: 'warning' };
      if (health === 'paused') return { label: t('dashboard.summary.chip.paused'), kind: 'warning' };
    }
    if (status.status.kind !== 'idle') return { label: t('dashboard.summary.chip.running'), kind: 'success' };
    return { label: t('dashboard.summary.chip.idle'), kind: 'neutral' };
  };

  if (memberAgentIds.length === 0) {
    return <span className="text-xs text-[var(--nm-ink30)]">—</span>;
  }

  const visible = memberAgentIds.slice(0, max);
  const overflow = Math.max(0, memberAgentIds.length - max);

  return (
    <span
      data-testid="team-member-avatars"
      className={`flex flex-wrap items-center -space-x-2 pr-2 ${className}`}
      onClick={(event) => event.stopPropagation()}
    >
      <TooltipProvider delayDuration={180} skipDelayDuration={80}>
        {visible.map((agentId) => {
          const agent = agentsById.get(agentId);
          const status = statusById.get(agentId);
          const name = agent?.name || agentId;
          const cell = statusCellOf(status);
          const isCurrentUserOwner = Boolean(agent && currentUserId && agent.created_by === currentUserId);
          const ownerLabel = isCurrentUserOwner
            ? currentUserDisplayName || currentUserId || '—'
            : agent?.created_by || '—';
          const description = agent?.description?.trim() || t('pages.dashboard.agentProfileNoDescription');

          return (
            <Tooltip key={agentId}>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  aria-label={t('pages.dashboard.openProfile', { name })}
                  data-testid={`team-member-avatar-${agentId}`}
                  onClick={() =>
                    navigate(`/app/agents/${encodeURIComponent(agentId)}`, { state: { from: 'dashboard' } })
                  }
                  className="relative inline-flex rounded-full outline-none transition-transform hover:z-10 hover:-translate-y-0.5 focus-visible:z-10 focus-visible:ring-2 focus-visible:ring-[var(--nm-ink30)]"
                >
                  <RingAvatar species="silicon" label={name.slice(0, 2)} size="sm" />
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
                <div className="flex items-start gap-2.5">
                  <RingAvatar species="silicon" label={name.slice(0, 2)} size="md" className="shrink-0" />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <span className="truncate text-[13px] font-semibold text-[var(--nm-ink)]">{name}</span>
                      {agent?.is_public ? (
                        <Globe
                          className="h-3 w-3 shrink-0 text-[var(--nm-ink50)]"
                          aria-label={t('pages.dashboard.memberPublic')}
                        />
                      ) : (
                        <Lock
                          className="h-3 w-3 shrink-0 text-[var(--nm-ink50)]"
                          aria-label={t('pages.dashboard.memberPrivate')}
                        />
                      )}
                    </div>
                    <span className="mt-0.5 inline-flex items-center gap-1.5 text-[10px] text-[var(--nm-ink50)]">
                      <StatusDot status={cell.kind} size={8} pulse={cell.kind === 'success'} />
                      {cell.label}
                    </span>
                  </div>
                </div>
                <p className="mt-2.5 line-clamp-3 whitespace-pre-line text-[11px] leading-relaxed text-[var(--nm-ink70)]">
                  {description}
                </p>
                <div className="mt-2.5 space-y-1 border-t border-[var(--nm-hairline)] pt-2">
                  <MetaRow label={t('pages.dashboard.memberRuntimeLabel')} value={formatFrameworkFromList(agent?.agent_framework, undefined)} />
                  <MetaRow label={t('pages.dashboard.memberModelLabel')} value={agent?.model || '—'} />
                  <MetaRow label={t('pages.dashboard.memberOwnerLabel')} value={ownerLabel} />
                </div>
              </TooltipContent>
            </Tooltip>
          );
        })}
        {overflow > 0 && (
          <Tooltip>
            <TooltipTrigger asChild>
              <span
                data-testid="team-member-avatars-overflow"
                className="relative inline-flex items-center pl-1 text-[11px] font-medium text-[var(--nm-ink50)]"
              >
                {`+${overflow}`}
              </span>
            </TooltipTrigger>
            <TooltipContent side="top" align="start">
              {t('pages.dashboard.membersCount', { count: memberAgentIds.length })}
            </TooltipContent>
          </Tooltip>
        )}
      </TooltipProvider>
    </span>
  );
}

function MetaRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 text-[11px]">
      <span className="text-[var(--nm-ink50)]">{label}</span>
      <span className="max-w-[140px] truncate text-[var(--nm-ink)]">{value}</span>
    </div>
  );
}

export default TeamMemberAvatars;
