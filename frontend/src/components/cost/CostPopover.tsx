/**
 * @file_name: CostPopover.tsx
 * @author: Bin Liang
 * @date: 2026-03-12
 * @description: Cost tracking popover - shows LLM API spend summary
 *
 * Displays a small dollar-sign button next to the inbox bell.
 * Click to open a popover with total spend, per-model breakdown, and daily trend.
 */

import { useState } from 'react';
import { DollarSign, RefreshCw, HelpCircle } from 'lucide-react';
import { Popover, PopoverTrigger, PopoverContent } from '@/components/ui/popover';
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from '@/components/ui/tooltip';
import { Button } from '@/components/ui';
import { usePreloadStore, useConfigStore } from '@/stores';
import { cn } from '@/lib/utils';
import type { CostSummary } from '@/types/api';

/** Format USD cost for display */
function formatCost(usd: number): string {
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  if (usd < 1) return `$${usd.toFixed(3)}`;
  return `$${usd.toFixed(2)}`;
}

/** Format token count (e.g. 12345 -> "12.3k") */
function formatTokens(n: number): string {
  if (n < 1000) return n.toString();
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

/** Short model name for display (drop date suffixes) */
function shortModelName(model: string): string {
  if (model === 'claude-code') return 'Claude Code';
  // "gpt-5.1-2025-11-13" -> "gpt-5.1"
  return model.replace(/-\d{4}-?\d{2}-?\d{2}$/, '').replace(/-\d{8}$/, '');
}

function SummaryContent({ summary }: { summary: CostSummary }) {
  const models = Object.entries(summary.by_model).sort(
    ([, a], [, b]) => b.cost - a.cost
  );

  return (
    <div className="space-y-3">
      {/* Total */}
      <div className="text-center pb-2 border-b border-[var(--border-subtle)]">
        <div className="text-2xl font-bold text-[var(--text-primary)]">
          {formatCost(summary.total_cost_usd)}
        </div>
        <div className="text-[10px] text-[var(--text-tertiary)] mt-0.5">
          {formatTokens(summary.total_input_tokens)} in / {formatTokens(summary.total_output_tokens)} out
        </div>
      </div>

      {/* Per-model breakdown */}
      {models.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-[10px] font-medium text-[var(--text-tertiary)] uppercase tracking-wider">
            By Model
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
                  {formatCost(data.cost)}
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
            Daily
          </div>
          {summary.daily.slice(-5).map((entry) => (
            <div key={entry.date} className="flex items-center justify-between text-xs">
              <span className="text-[var(--text-tertiary)]">{entry.date.slice(5)}</span>
              <span className="font-medium text-[var(--text-primary)]">
                {formatCost(entry.cost)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function CostPopover() {
  const [isRefreshing, setIsRefreshing] = useState(false);
  const { agentId } = useConfigStore();
  const { costSummary, costLoading, refreshCost } = usePreloadStore();

  const handleRefresh = async () => {
    setIsRefreshing(true);
    try {
      await refreshCost(agentId);
    } finally {
      setIsRefreshing(false);
    }
  };

  const todayCost = costSummary?.daily?.find(
    (d) => d.date === new Date().toISOString().slice(0, 10)
  )?.cost ?? 0;

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" className="relative" title="API Cost">
          <DollarSign className="w-5 h-5" />
          {todayCost > 0 && (
            <span className="absolute -top-1 -right-1 h-4 min-w-4 px-0.5 flex items-center justify-center text-[9px] font-medium bg-[var(--accent-primary)] text-white rounded-full">
              {formatCost(todayCost)}
            </span>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        sideOffset={8}
        className="w-[260px] p-3 bg-[var(--bg-primary)] border border-[var(--border-default)] rounded-xl shadow-lg"
      >
        {/* Header */}
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-1">
            <span className="text-xs font-medium text-[var(--text-secondary)]">
              API Cost (7d)
            </span>
            <TooltipProvider delayDuration={0}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <button className="text-[var(--text-tertiary)] hover:text-[var(--text-secondary)] transition-colors">
                    <HelpCircle className="w-3 h-3" />
                  </button>
                </TooltipTrigger>
                <TooltipContent side="bottom" className="max-w-[240px] text-xs leading-relaxed">
                  <p className="font-medium mb-1">How costs are calculated</p>
                  <p>
                    Costs are computed from token usage reported by each LLM SDK.
                    For Claude Code (Agent Loop), the SDK returns a total_cost_usd
                    based on list pricing — this reflects equivalent API spend,
                    not your actual bill if you are on a Max subscription.
                  </p>
                  <p className="mt-1">
                    For OpenAI and Gemini calls, costs are calculated from our
                    built-in per-model price table.
                  </p>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          </div>
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6"
            onClick={handleRefresh}
            disabled={isRefreshing || costLoading}
          >
            <RefreshCw className={cn('w-3 h-3', (isRefreshing || costLoading) && 'animate-spin')} />
          </Button>
        </div>

        {/* Content */}
        {costLoading && !costSummary ? (
          <div className="py-4 text-center text-xs text-[var(--text-tertiary)]">Loading...</div>
        ) : costSummary ? (
          <SummaryContent summary={costSummary} />
        ) : (
          <div className="py-4 text-center text-xs text-[var(--text-tertiary)]">
            No cost data yet
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}
