/**
 * @file_name: AgentCard.tsx
 * @description: v2.1.2 — agent card with two-tier visibility (collapsed vs
 * expanded) driven by card-body click (inner buttons stopPropagation).
 *
 * Changes vs v2.1.1:
 *   - Card body itself is clickable again (regression fix). Inner interactive
 *     sections (banners, section headers, items, action buttons) all call
 *     e.stopPropagation() in their own handlers, so clicking a session row
 *     or a Retry button doesn't bubble up and toggle the whole card.
 *   - When all attention banners are dismissed in sessionStorage, the status
 *     rail dims (opacity-40) instead of staying bright red/amber. The
 *     underlying health is still "error" or "warning" (server-driven), but
 *     the visual urgency drops once the user has acknowledged. If count
 *     changes (new failure), banners re-appear and the rail un-dims.
 *
 * Layout (owned agents):
 *   COLLAPSED (default):
 *     Header · verb_line · banners · inline queue+metrics + ▾ more hint
 *   EXPANDED:
 *     above + sessions + jobs + sparkline + recent feed
 */
import type { AgentStatus, OwnedAgentStatus, AttentionBanner } from '@/types';
import { StatusBadge } from './StatusBadge';
import { DurationDisplay } from './DurationDisplay';
import { ConcurrencyBadge } from './ConcurrencyBadge';
import { AttentionBanners } from './AttentionBanners';
import { SessionSection } from './SessionSection';
import { JobsSection } from './JobsSection';
import { QueueBar } from './QueueBar';
import { Sparkline } from './Sparkline';
import { RecentFeed } from './RecentFeed';
import { MetricsRow } from './MetricsRow';
import { HEALTH_COLORS } from './healthColors';
import { useAllBannersDismissed, bannerKey } from './expandState';

const HEALTH_TOOLTIP = {
  healthy_running: 'Healthy · running',
  healthy_idle: 'Healthy · idle (recently active)',
  idle_long: 'Quiet · idle > 72h',
  warning: 'Warning · job blocked',
  paused: 'Paused · jobs paused by user',
  error: 'Error · failed job or error event',
} as const;

interface Props {
  agent: AgentStatus;
  onToggleExpand: () => void;
  expanded?: boolean;
}

export function AgentCard({ agent, onToggleExpand, expanded }: Props) {
  if (!agent.owned_by_viewer) {
    return <PublicCard agent={agent} />;
  }
  return <OwnedCard agent={agent} expanded={!!expanded} onToggleExpand={onToggleExpand} />;
}

function PublicCard({ agent }: { agent: AgentStatus }) {
  const colors = HEALTH_COLORS.healthy_idle;
  return (
    <div
      data-testid={`agent-card-${agent.agent_id}`}
      className="group flex overflow-hidden rounded-xl border border-[var(--border-primary)] bg-[var(--bg-glass)]"
    >
      <div className={`w-1 shrink-0 ${colors.rail}`} aria-hidden />
      <div className="flex-1 p-3 min-w-0">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <span className="truncate font-semibold text-sm">{agent.name}</span>
            <StatusBadge kind={agent.status.kind} />
            <ConcurrencyBadge agent={agent} />
          </div>
          <DurationDisplay startedAt={agent.status.started_at} />
        </div>
        {agent.description && (
          <div className="mt-1 text-xs text-[var(--text-secondary)] italic truncate">
            {agent.description}
          </div>
        )}
      </div>
    </div>
  );
}

function OwnedCard({
  agent,
  expanded,
  onToggleExpand,
}: {
  agent: OwnedAgentStatus;
  expanded: boolean;
  onToggleExpand: () => void;
}) {
  const banners = agent.attention_banners ?? [];
  // Key list for dismiss state lookup — must match the format used in
  // AttentionBanners.BannerRow (see expandState.bannerKey).
  const allKeys = banners.map((b: AttentionBanner) => bannerKey(agent.agent_id, b.kind, b.message));
  const allDismissed = useAllBannersDismissed(allKeys);

  const colors = HEALTH_COLORS[agent.health];
  // When the user has acknowledged every banner, dim the rail to de-escalate.
  // We keep the semantic color (still red/amber) but reduce opacity so the
  // card visually quiets down. It un-dims automatically if a new banner appears
  // (new signature → new storage key → not dismissed yet → allDismissed=false).
  const railDimClass = allDismissed ? 'opacity-40' : '';
  const verbLine = agent.verb_line;
  const hasSessions = agent.sessions.length > 0;
  const hasJobs = agent.running_jobs.length > 0 || agent.pending_jobs.length > 0;
  const hasRecent = agent.recent_events.length > 0;
  // Same idea on card-body tint: drop the red wash if acknowledged.
  const cardTint = allDismissed ? '' : colors.cardTint;

  return (
    <div
      data-testid={`agent-card-${agent.agent_id}`}
      data-expanded={expanded ? 'true' : 'false'}
      data-health={agent.health}
      data-banners-dismissed={allDismissed ? 'true' : 'false'}
      role="button"
      tabIndex={0}
      aria-expanded={expanded}
      onClick={onToggleExpand}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onToggleExpand();
        }
      }}
      className={`group flex overflow-hidden rounded-xl border border-[var(--border-primary)] bg-[var(--bg-glass)] cursor-pointer hover:border-[var(--accent-primary)] transition-colors ${cardTint} ${agent.health === 'idle_long' ? 'opacity-75' : ''}`}
    >
      <div
        className={`w-1 shrink-0 ${colors.rail} ${railDimClass} transition-opacity`}
        title={HEALTH_TOOLTIP[agent.health] + (allDismissed ? ' · acknowledged' : '')}
        aria-hidden
      />
      <div className="flex-1 p-3 min-w-0">
        {/* Header — name + kind + duration */}
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <span className="truncate font-semibold text-sm">{agent.name}</span>
            <StatusBadge kind={agent.status.kind} />
          </div>
          <DurationDisplay startedAt={agent.status.started_at} />
        </div>

        {/* Verb line (always — primary narrative) */}
        {verbLine && (
          <div className={`mt-1 text-sm ${colors.text}`} data-testid="verb-line">
            {verbLine}
          </div>
        )}

        {/* Banners (each dismissible individually) */}
        <AttentionBanners agentId={agent.agent_id} banners={banners} />

        {/* Inline summary row — visible in both collapsed + expanded modes */}
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1">
          <QueueBar queue={agent.queue} compact />
          <MetricsRow metrics={agent.metrics_today} />
          <span className="ml-auto text-[11px] text-[var(--text-secondary)]">
            {expanded ? '▴ less' : '▾ more'}
          </span>
        </div>

        {/* Expanded sections */}
        {expanded && (
          <div className="mt-3 space-y-2 border-t border-[var(--border-primary)]/50 pt-3">
            {hasSessions && (
              <SessionSection agentId={agent.agent_id} sessions={agent.sessions} />
            )}
            {hasJobs && (
              <JobsSection
                agentId={agent.agent_id}
                runningJobs={agent.running_jobs}
                pendingJobs={agent.pending_jobs}
              />
            )}
            <Sparkline agentId={agent.agent_id} health={agent.health} />
            {hasRecent && (
              <RecentFeed agentId={agent.agent_id} events={agent.recent_events} />
            )}
          </div>
        )}
      </div>
    </div>
  );
}
