/**
 * @file_name: AgentActivityCard.tsx
 * @author: NexusAgent
 * @date: 2026-08-27
 * @description: The Agent profile's "how has this agent been doing lately"
 * band — the only time-dimensioned surface on the page.
 *
 * Four read-only views of the same live status payload, arranged by how
 * often you need them: the 24h shape and today's totals get the split top
 * half, live sessions and the last few events sit below the rule as
 * secondary detail.
 *
 * Everything here comes from the `OwnedAgentStatus` the page already holds,
 * except Sparkline, which lazily fetches its own buckets. A public agent
 * carries no OwnedAgentStatus at all, so the caller renders nothing —
 * this component never has to reason about foreign agents.
 */
import { useTranslation } from 'react-i18next';
import { PaperCard } from '@/components/nm';
import { Sparkline } from '@/components/dashboard/Sparkline';
import { MetricsRow } from '@/components/dashboard/MetricsRow';
import { SessionSection } from '@/components/dashboard/SessionSection';
import { RecentFeed } from '@/components/dashboard/RecentFeed';
import type { OwnedAgentStatus } from '@/types';

export interface AgentActivityCardProps {
  agentId: string;
  status: OwnedAgentStatus;
}

export function AgentActivityCard({ agentId, status }: AgentActivityCardProps) {
  const { t } = useTranslation();

  const hasSessions = status.sessions.length > 0;
  const hasEvents = status.recent_events.length > 0;

  return (
    <PaperCard padding="none" data-testid="agent-activity-card">
      <div className="border-b border-[var(--nm-hairline)] px-4 py-3">
        <h2 className="text-sm font-semibold text-[var(--nm-ink)]">
          {t('pages.agentProfile.activity')}
        </h2>
      </div>

      {/* Top half: the 24h shape next to today's totals. Stacks on narrow
          viewports — the sparkline needs its full width to stay readable, so
          it must not share a row with the metrics below ~640px. */}
      <div className="grid grid-cols-1 sm:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
        <div className="px-4 py-4">
          <span className="mb-2.5 block font-mono text-[9px] uppercase tracking-[0.14em] text-[var(--nm-ink30)]">
            {t('pages.agentProfile.activityLast24h')}
          </span>
          <Sparkline agentId={agentId} health={status.health} />
          <div className="mt-1.5 flex justify-between font-mono text-[9px] text-[var(--nm-ink30)]">
            <span>{t('pages.agentProfile.activityAxisStart')}</span>
            <span>{t('pages.agentProfile.activityAxisNow')}</span>
          </div>
        </div>
        <div className="border-t border-[var(--nm-hairline)] px-4 py-4 sm:border-l sm:border-t-0">
          <span className="mb-2.5 block font-mono text-[9px] uppercase tracking-[0.14em] text-[var(--nm-ink30)]">
            {t('pages.agentProfile.activityToday')}
          </span>
          <MetricsRow metrics={status.metrics_today} />
        </div>
      </div>

      {/* Secondary detail. Both children self-hide when empty, so the rule and
          its padding must be conditional too — otherwise a quiet agent gets a
          bare 40px strip under the metrics. */}
      {(hasSessions || hasEvents) && (
        <div
          data-testid="agent-activity-detail"
          className="flex flex-col gap-3 border-t border-[var(--nm-hairline)] px-4 py-3"
        >
          {hasSessions && <SessionSection agentId={agentId} sessions={status.sessions} />}
          {hasEvents && <RecentFeed agentId={agentId} events={status.recent_events} />}
        </div>
      )}
    </PaperCard>
  );
}

export default AgentActivityCard;
