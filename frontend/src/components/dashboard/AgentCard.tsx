/**
 * @file_name: AgentCard.tsx
 * @description: v2.1 — rich agent card with status rail + banners + sections
 * + sparkline + metrics. Progressive disclosure: header is always visible,
 * sections expand individually, each item (session/job) has its own expand.
 *
 * Card layout (top to bottom):
 *   [Header]                 name · kind · duration · hover actions
 *   [AttentionBanners]       failed / blocked / paused (only when present)
 *   [Verb line]              "Serving 3 users" / "Running: weekly-report"
 *   [SessionSection]         💬 3 sessions [A][B][C] on Lark · Web  ▸
 *   [JobsSection]            ⚙️ Jobs (4) · 2 running  ▸
 *   [QueueBar]               stacked bar + counts
 *   [Sparkline]              24h activity bars
 *   [RecentFeed]             Recent (3) ▸
 *   [MetricsRow]             ✓ 47  ⚠ 1  ⏱ 2.1s  $0.12
 *
 * For public non-owned agents, most sections are hidden — only header +
 * name + bucketed concurrency are rendered.
 */
import type { AgentStatus } from '@/types';
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

const EM_DASH = '\u2014';

interface Props {
  agent: AgentStatus;
  onToggleExpand: () => void;
  expanded?: boolean;
}

export function AgentCard({ agent, onToggleExpand, expanded }: Props) {
  const isOwned = agent.owned_by_viewer;
  const health = isOwned ? agent.health : 'healthy_idle';
  const colors = HEALTH_COLORS[health];
  const verbLine = isOwned ? agent.verb_line : null;
  const actionLine = isOwned ? agent.action_line : null;

  return (
    <div
      data-testid={`agent-card-${agent.agent_id}`}
      data-expanded={expanded ? 'true' : 'false'}
      data-health={health}
      onClick={onToggleExpand}
      className={`group relative flex overflow-hidden rounded-xl border border-[var(--border-primary)] bg-[var(--bg-glass)] cursor-pointer hover:border-[var(--accent-primary)] transition-colors ${colors.cardTint}`}
    >
      <div className={`w-1 shrink-0 ${colors.rail}`} aria-hidden />
      <div className="flex-1 p-3 space-y-2 min-w-0">
        {/* Header */}
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <span className="truncate font-semibold text-sm">{agent.name}</span>
            <StatusBadge kind={agent.status.kind} />
            <ConcurrencyBadge agent={agent} />
          </div>
          <DurationDisplay startedAt={agent.status.started_at} />
        </div>

        {/* Owner-only rich content */}
        {isOwned ? (
          <>
            {/* Banners */}
            <AttentionBanners banners={agent.attention_banners ?? []} />

            {/* Verb line */}
            {verbLine && (
              <div className={`text-sm ${colors.text}`} data-testid="verb-line">
                {verbLine}
              </div>
            )}

            {/* Action-line preview (context for what's currently happening) */}
            {actionLine && actionLine !== verbLine && (
              <div
                data-testid="action-line"
                className="text-xs text-[var(--text-secondary)] truncate"
                title={actionLine}
              >
                {actionLine}
              </div>
            )}

            {/* Sections */}
            {agent.sessions.length > 0 && (
              <SessionSection agentId={agent.agent_id} sessions={agent.sessions} />
            )}
            {(agent.running_jobs.length > 0 || agent.pending_jobs.length > 0) && (
              <JobsSection
                agentId={agent.agent_id}
                runningJobs={agent.running_jobs}
                pendingJobs={agent.pending_jobs}
              />
            )}

            {/* Queue bar + sparkline (always visible for owned) */}
            <QueueBar queue={agent.queue} />
            <Sparkline agentId={agent.agent_id} health={agent.health} />

            {/* Recent feed (collapsible) */}
            {agent.recent_events.length > 0 && (
              <RecentFeed agentId={agent.agent_id} events={agent.recent_events} />
            )}

            {/* Metrics footer */}
            <MetricsRow metrics={agent.metrics_today} />
          </>
        ) : (
          // Public non-owned: minimal
          <div className="text-xs text-[var(--text-secondary)] italic">
            {agent.description ?? EM_DASH}
          </div>
        )}
      </div>
    </div>
  );
}
