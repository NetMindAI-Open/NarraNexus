/**
 * @file_name: AgentCard.tsx
 * @description: v2.1.1 — agent card with two-tier visibility (collapsed vs
 * expanded). Goal: reduce visual noise so the user sees only what matters at
 * a glance, and gets richer detail by clicking.
 *
 * COLLAPSED (default state, what most cards always look like):
 *   ┌─▋ Alpha · 💬 · ⏱ 12s ─────────────────────────────────┐
 *   │ ▋ Serving 3 users [A][B][C]                            │  ← verb_line + avatars
 *   │ ▋ 🔴 1 job failed 3m ago                  [Retry] [×] │  ← banner (dismissible)
 *   │ ▋ Queue ████▓░░░ 5  ·  ✓ 47  ⚠ 1  $0.12  ·  ▾ more   │  ← inline metrics + expand
 *   └────────────────────────────────────────────────────────┘
 *
 * EXPANDED (after click — full sections visible):
 *   above + SessionSection + JobsSection + Sparkline + RecentFeed
 *
 * Public non-owned variant: only the header line (no verb, no sections).
 * Permission boundary preserved by the discriminated union; here it's an
 * `if` early return.
 */
import type { AgentStatus, OwnedAgentStatus } from '@/types';
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
  const colors = HEALTH_COLORS[agent.health];
  const verbLine = agent.verb_line;
  const hasSessions = agent.sessions.length > 0;
  const hasJobs = agent.running_jobs.length > 0 || agent.pending_jobs.length > 0;
  const hasRecent = agent.recent_events.length > 0;

  return (
    <div
      data-testid={`agent-card-${agent.agent_id}`}
      data-expanded={expanded ? 'true' : 'false'}
      data-health={agent.health}
      className={`group flex overflow-hidden rounded-xl border border-[var(--border-primary)] bg-[var(--bg-glass)] transition-colors ${colors.cardTint} ${agent.health === 'idle_long' ? 'opacity-75' : ''}`}
    >
      <div
        className={`w-1 shrink-0 ${colors.rail}`}
        title={HEALTH_TOOLTIP[agent.health]}
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

        {/* Banners */}
        <AttentionBanners agentId={agent.agent_id} banners={agent.attention_banners ?? []} />

        {/* Inline summary row — visible in both collapsed + expanded modes */}
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1">
          <QueueBar queue={agent.queue} compact />
          <MetricsRow metrics={agent.metrics_today} />
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onToggleExpand(); }}
            className="ml-auto text-[11px] text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
            aria-expanded={expanded}
          >
            {expanded ? '▴ less' : '▾ more'}
          </button>
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
