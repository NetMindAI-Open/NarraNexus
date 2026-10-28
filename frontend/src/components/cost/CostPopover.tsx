/**
 * @file_name: CostPopover.tsx
 * @author: Bin Liang
 * @date: 2026-03-12
 * @description: Token usage popover - shows LLM API token consumption summary
 *
 * Supports two views: current agent and all agents combined.
 */

import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { RefreshCw } from 'lucide-react';
import { Popover, PopoverTrigger, PopoverContent } from '@/components/ui/popover';
import { Button } from '@/components/ui';
import { usePreloadStore, useConfigStore, useChatStore } from '@/stores';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';
// Shared with the account page's NarraNexus-usage section — the same week of
// usage must not read 1.2M here and 1.23M there. See lib/tokenFormat.ts.
import {
  formatCost,
  formatTokens,
  shortModelName,
  summaryInputSideTokens,
  summaryTotalTokens,
  totalTokens,
} from '@/lib/tokenFormat';
import type { CostModelBreakdown, CostSummary } from '@/types';

type CostView = 'agent' | 'all';

function SummaryContent({ summary }: { summary: CostSummary }) {
  const { t } = useTranslation();
  // The three-input-bucket rule lives in lib/tokenFormat.ts — see it for why
  // input_tokens alone under-reports a cache-warm agent by an order of
  // magnitude, and why `?? 0` is load-bearing.
  const cacheRead = summary.total_cache_read_tokens ?? 0;
  const cacheWrite = summary.total_cache_creation_tokens ?? 0;
  const totalInputSide = summaryInputSideTokens(summary);
  const grandTotal = summaryTotalTokens(summary);
  const modelTokens = (d: CostModelBreakdown) => totalTokens(d);

  // The raw token total reads scary on a cache-warm agent — 1.2M of it may be
  // 0.1x-priced cache reads. The hit rate carries the good news visibly
  // (rate-free math); real cost lives in hover tooltips on the token figures
  // (owner preference: no money on the face of the panel). Cost tooltips
  // appear only when > 0 — unpriced models book $0 and a "$0.00" would read
  // as "free" rather than "unknown".
  const cacheHitRate =
    totalInputSide > 0 ? Math.round((cacheRead / totalInputSide) * 100) : 0;
  const totalCostTip =
    summary.total_cost_usd > 0
      ? t('cost.popover.totalCost', { cost: formatCost(summary.total_cost_usd) })
      : undefined;
  const models = Object.entries(summary.by_model).sort(
    ([, a], [, b]) => modelTokens(b) - modelTokens(a)
  );

  return (
    <div className="space-y-3">
      {/* Total */}
      <div className="text-center pb-2 border-b border-[var(--border-subtle)]">
        <div
          className="text-2xl font-bold text-[var(--text-primary)]"
          title={totalCostTip}
        >
          {formatTokens(grandTotal)}
        </div>
        <div className="text-[10px] text-[var(--text-tertiary)] mt-0.5">
          {t('cost.popover.inOut', { in: formatTokens(totalInputSide), out: formatTokens(summary.total_output_tokens) })}
        </div>
        {cacheRead > 0 && (
          <div
            className="text-[10px] text-[var(--text-tertiary)] mt-0.5"
            title={t('cost.popover.cacheDetail', {
              read: formatTokens(cacheRead),
              write: formatTokens(cacheWrite),
            })}
          >
            {t('cost.popover.cacheHit', { rate: cacheHitRate })}
          </div>
        )}
      </div>

      {/* Per-model breakdown */}
      {models.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-[10px] font-medium text-[var(--text-tertiary)] uppercase tracking-wider">
            {t('cost.popover.byModel')}
          </div>
          {models.map(([model, data]) => (
            <div key={model} className="flex items-center justify-between text-xs">
              <span className="text-[var(--text-secondary)] truncate max-w-[140px]" title={model}>
                {shortModelName(model, {
                  main: t('cost.popover.modelUsage'),
                  helper: t('cost.popover.helperUsage'),
                })}
              </span>
              <div className="flex items-center gap-2">
                <span className="text-[10px] text-[var(--text-tertiary)]">
                  x{data.call_count}
                </span>
                <span
                  className="font-medium text-[var(--text-primary)] min-w-[50px] text-right"
                  title={data.cost > 0 ? formatCost(data.cost) : undefined}
                >
                  {formatTokens(modelTokens(data))}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Daily trend (last few days) */}
      {summary.daily.length > 0 && (
        <div className="space-y-1.5 pt-1 border-t border-[var(--border-subtle)]">
          <div className="text-[10px] font-medium text-[var(--text-tertiary)] uppercase tracking-wider">
            {t('cost.popover.daily')}
          </div>
          {summary.daily.slice(-5).map((entry) => (
            <div key={entry.date} className="flex items-center justify-between text-xs">
              <span className="text-[var(--text-tertiary)]">{entry.date.slice(5)}</span>
              <span className="font-medium text-[var(--text-primary)]">
                {formatTokens(totalTokens(entry))}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function CostPopover({ compact = false }: { compact?: boolean } = {}) {
  const { t } = useTranslation();
  const [view, setView] = useState<CostView>('agent');
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [allSummary, setAllSummary] = useState<CostSummary | null>(null);
  const [allLoading, setAllLoading] = useState(false);

  const { agentId } = useConfigStore();
  const { costSummary, costLoading, refreshCost } = usePreloadStore();
  // The current agent's live state drives the icon: streaming/thinking → pulse,
  // otherwise a calm idle glow.
  const working = useChatStore((s) => s.isStreaming);

  const activeSummary = view === 'agent' ? costSummary : allSummary;
  const activeLoading = view === 'agent' ? costLoading : allLoading;

  const loadAllAgents = useCallback(async () => {
    setAllLoading(true);
    try {
      const res = await api.getCosts('_all');
      if (res.success && res.summary) {
        setAllSummary(res.summary);
      }
    } catch {
      // Silently ignore
    } finally {
      setAllLoading(false);
    }
  }, []);

  // Load "all agents" data on first switch
  useEffect(() => {
    if (view === 'all' && !allSummary && !allLoading) {
      loadAllAgents();
    }
  }, [view, allSummary, allLoading, loadAllAgents]);

  const handleRefresh = async () => {
    setIsRefreshing(true);
    try {
      if (view === 'agent') {
        await refreshCost(agentId);
      } else {
        await loadAllAgents();
      }
    } finally {
      setIsRefreshing(false);
    }
  };

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" className={cn('relative', compact && 'h-7 w-7')} title={t('cost.popover.triggerTitle')}>
          {/* The activity glyph IS the agent's heartbeat, drawn as an ECG: a
              bright pulse sweeps the waveform while it streams/thinks, and the
              line just glows softly when idle. No running token count here —
              that figure read as an anxiety-inducing meter; the numbers live
              inside the popover, on click. */}
          <svg
            className={cn(compact ? 'w-4 h-4' : 'w-5 h-5', working ? 'nm-activity-working' : 'nm-activity-idle')}
            viewBox="0 0 24 24"
            fill="none"
            strokeWidth={2}
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden
          >
            <path className="nm-ecg-base" d="M2 12 L6 12 L9 3 L15 21 L18 12 L22 12" />
            <path className="nm-ecg-pulse" pathLength={100} d="M2 12 L6 12 L9 3 L15 21 L18 12 L22 12" />
          </svg>
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        sideOffset={8}
        className="w-[260px] p-3 bg-[var(--bg-primary)] border border-[var(--border-default)] rounded-[var(--radius-xl)] shadow-lg"
      >
        {/* Header with view toggle */}
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-1 p-0.5 bg-[var(--bg-tertiary)] rounded-[var(--radius-md)]">
            <button
              onClick={() => setView('agent')}
              className={cn(
                'px-2 py-0.5 rounded text-[10px] font-medium transition-all',
                view === 'agent'
                  ? 'bg-[var(--bg-primary)] text-[var(--text-primary)] shadow-sm'
                  : 'text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]'
              )}
            >
              {t('cost.popover.viewAgent')}
            </button>
            <button
              onClick={() => setView('all')}
              className={cn(
                'px-2 py-0.5 rounded text-[10px] font-medium transition-all',
                view === 'all'
                  ? 'bg-[var(--bg-primary)] text-[var(--text-primary)] shadow-sm'
                  : 'text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]'
              )}
            >
              {t('cost.popover.viewAll')}
            </button>
          </div>
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6"
            onClick={handleRefresh}
            disabled={isRefreshing || activeLoading}
          >
            <RefreshCw className={cn('w-3 h-3', (isRefreshing || activeLoading) && 'animate-spin')} />
          </Button>
        </div>

        {/* Subtitle */}
        <div className="text-[10px] text-[var(--text-tertiary)] mb-2">
          {view === 'agent' ? t('cost.popover.subtitleAgent') : t('cost.popover.subtitleAll')}
        </div>

        {/* Content */}
        {activeLoading && !activeSummary ? (
          <div className="py-4 text-center text-xs text-[var(--text-tertiary)]">{t('cost.popover.loading')}</div>
        ) : activeSummary ? (
          <SummaryContent summary={activeSummary} />
        ) : (
          <div className="py-4 text-center text-xs text-[var(--text-tertiary)]">
            {t('cost.popover.noData')}
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}
