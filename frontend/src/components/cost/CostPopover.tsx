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
import type { CostSummary } from '@/types/api';

type CostView = 'agent' | 'all';

/** Format token count (e.g. 12345 -> "12.3k") */
function formatTokens(n: number): string {
  if (n < 1000) return n.toString();
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

/** Short model name for display (drop date suffixes) */
function shortModelName(model: string): string {
  if (model === 'claude-code') return 'Claude Code';
  return model.replace(/-\d{4}-?\d{2}-?\d{2}$/, '').replace(/-\d{8}$/, '');
}

function SummaryContent({ summary }: { summary: CostSummary }) {
  const { t } = useTranslation();
  const totalTokens = summary.total_input_tokens + summary.total_output_tokens;
  const models = Object.entries(summary.by_model).sort(
    ([, a], [, b]) => (b.input_tokens + b.output_tokens) - (a.input_tokens + a.output_tokens)
  );

  return (
    <div className="space-y-3">
      {/* Total */}
      <div className="text-center pb-2 border-b border-[var(--border-subtle)]">
        <div className="text-2xl font-bold text-[var(--text-primary)]">
          {formatTokens(totalTokens)}
        </div>
        <div className="text-[10px] text-[var(--text-tertiary)] mt-0.5">
          {t('cost.popover.inOut', { in: formatTokens(summary.total_input_tokens), out: formatTokens(summary.total_output_tokens) })}
        </div>
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
                {shortModelName(model)}
              </span>
              <div className="flex items-center gap-2">
                <span className="text-[10px] text-[var(--text-tertiary)]">
                  x{data.call_count}
                </span>
                <span className="font-medium text-[var(--text-primary)] min-w-[50px] text-right">
                  {formatTokens(data.input_tokens + data.output_tokens)}
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
                {formatTokens(entry.input_tokens + entry.output_tokens)}
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
        className="w-[260px] p-3 bg-[var(--bg-primary)] border border-[var(--border-default)] rounded-xl shadow-lg"
      >
        {/* Header with view toggle */}
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-1 p-0.5 bg-[var(--bg-tertiary)] rounded-md">
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
