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
import { HEALTH_COLORS, acknowledgedHealthOf } from './healthColors';
import { useAllBannersDismissed, bannerKey } from './expandState';

const HEALTH_TOOLTIP = {
  healthy_running: 'Healthy · running',
  healthy_idle: 'Healthy · idle (recently active)',
  idle_long: 'Quiet · idle > 72h',
  warning: 'Warning · job blocked',
  paused: 'Paused · jobs paused by user',
  error: 'Error · failed job or error event',
  acknowledged: 'Error acknowledged · underlying issue still present',
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
      className="group flex overflow-hidden rounded-xl border border-[var(--border-default)] bg-[var(--bg-primary)]"
    >
      <div className={`w-1 shrink-0 ${colors.rail}`} aria-hidden />
      <div className="flex-1 p-3 min-w-0">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-2 min-w-0">
            <span className="truncate font-semibold text-sm">{agent.name}</span>
            <StatusBadge kind={agent.status.kind} />
            <ConcurrencyBadge agent={agent} />
          </div>
          <div className="shrink-0 flex items-baseline gap-1 text-[11px] text-[var(--text-tertiary)] tabular-nums">
            <span className="uppercase tracking-[0.08em] font-[family-name:var(--font-mono)]">
              {agent.status.kind === 'idle' ? 'idle' : 'active'}
            </span>
            <span className="text-[var(--text-secondary)] font-medium">
              <DurationDisplay startedAt={agent.status.started_at} />
            </span>
          </div>
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
  const staleInstances = agent.stale_instances ?? [];
  const allKeys = banners.map((b: AttentionBanner) =>
    bannerKey(agent.agent_id, b.kind, b.message),
  );
  const allDismissed = useAllBannersDismissed(allKeys);

  // v2.2 G2: derive what to render. error NEVER downgrades to healthy
  // (Security-M1) — it becomes 'acknowledged' (slate rail + red dot).
  const effectiveHealth = acknowledgedHealthOf(
    agent.health,
    allDismissed,
    agent.status.kind,
  );
  const colors = HEALTH_COLORS[effectiveHealth];
  const isAcknowledgedError = effectiveHealth === 'acknowledged';

  const verbLine = agent.verb_line;
  const hasSessions = agent.sessions.length > 0;
  const hasJobs = agent.running_jobs.length > 0 || agent.pending_jobs.length > 0;
  const hasRecent = agent.recent_events.length > 0;

  return (
    <div
      data-testid={`agent-card-${agent.agent_id}`}
      data-expanded={expanded ? 'true' : 'false'}
      data-health={effectiveHealth}
      data-server-health={agent.health}
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
      className={`group relative flex overflow-hidden border border-[var(--border-default)] bg-[var(--bg-primary)] hover:border-[var(--border-strong)] transition-colors duration-150 cursor-pointer ${colors.cardTint} ${agent.health === 'idle_long' ? 'opacity-75' : ''}`}
    >
      {/* Status rail with optional ack-dot (v2.2 G2 + G4) */}
      <div className="relative shrink-0">
        <div
          className={`w-1 h-full ${colors.rail} transition-colors duration-200`}
          title={HEALTH_TOOLTIP[effectiveHealth as keyof typeof HEALTH_TOOLTIP]}
          aria-hidden
        />
        {isAcknowledgedError && (
          <span
            data-testid="ack-dot"
            title="Error acknowledged — underlying issue still present"
            aria-label="error acknowledged"
            className="pointer-events-none absolute -right-[3px] top-2 h-2 w-2 rounded-full bg-[var(--color-red-500)] ring-2 ring-[var(--bg-elevated)]"
          />
        )}
      </div>

      <div className="flex-1 p-4 min-w-0">
        {/* Header — name + kind pill + stale badge + "active 30m" */}
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-2 min-w-0">
            <span className="truncate text-[15px] font-semibold tracking-tight text-[var(--text-primary)]">
              {agent.name}
            </span>
            <StatusBadge kind={agent.status.kind} />
            {staleInstances.length > 0 && (
              <span
                data-testid="stale-badge"
                title={`${staleInstances.length} zombie instance(s): ${staleInstances.map((s) => s.module_class).join(', ')}`}
                className="inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-[family-name:var(--font-mono)] uppercase tracking-[0.08em] text-[var(--text-tertiary)] border border-[var(--border-subtle)] rounded"
              >
                <span className="h-1 w-1 rounded-full allow-circle bg-[var(--text-tertiary)]" aria-hidden />
                {staleInstances.length} stale
              </span>
            )}
          </div>
          <div className="shrink-0 flex items-baseline gap-1 text-[11px] text-[var(--text-tertiary)] tabular-nums">
            <span className="uppercase tracking-[0.08em] font-[family-name:var(--font-mono)]">
              {agent.status.kind === 'idle' ? 'idle' : 'active'}
            </span>
            <span className="text-[var(--text-secondary)] font-medium">
              <DurationDisplay startedAt={agent.status.started_at} />
            </span>
          </div>
        </div>

        {/* Verb line — the one-sentence story of what this agent is doing */}
        {verbLine && (
          <div
            className={`mt-2 text-[13px] leading-snug ${colors.text}`}
            data-testid="verb-line"
          >
            {verbLine}
          </div>
        )}

        {/* Banners (each dismissible individually) */}
        <AttentionBanners agentId={agent.agent_id} banners={banners} />

        {/* Stats — queue first (workload), metrics second (today's totals) */}
        {(agent.queue.total > 0 || agent.metrics_today.runs_ok > 0 || agent.metrics_today.errors > 0) && (
          <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2">
            <QueueBar queue={agent.queue} compact />
            <MetricsRow metrics={agent.metrics_today} />
            <span className="ml-auto text-[11px] text-[var(--text-tertiary)] group-hover:text-[var(--accent-primary)] transition-colors duration-150 font-[family-name:var(--font-mono)] uppercase tracking-[0.08em]">
              {expanded ? '▴ less' : '▾ details'}
            </span>
          </div>
        )}
        {agent.queue.total === 0 && agent.metrics_today.runs_ok === 0 && agent.metrics_today.errors === 0 && (
          <div className="mt-3 flex justify-end">
            <span className="text-[11px] text-[var(--text-tertiary)] group-hover:text-[var(--accent-primary)] transition-colors duration-150 font-[family-name:var(--font-mono)] uppercase tracking-[0.08em]">
              {expanded ? '▴ less' : '▾ details'}
            </span>
          </div>
        )}

        {/* Expanded sections — grid 0fr→1fr animation (v2.2 G4) */}
        <div
          className={`grid transition-[grid-template-rows] duration-200 ease-out ${expanded ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'}`}
          aria-hidden={!expanded}
        >
          <div className="overflow-hidden">
            <div className="mt-3 space-y-2 border-t border-[var(--rule)]/50 pt-3">
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
          </div>
        </div>
      </div>
    </div>
  );
}
