/**
 * @file_name: MetricsRow.tsx
 * @description: v2.1 — today's counts at the card footer. Missing data
 * (token_cost_cents) renders as em-dash, not 0.
 */
import type { MetricsToday } from '@/types';

const EM_DASH = '\u2014';

function formatCost(cents: number | null): string {
  if (cents === null || cents === undefined) return EM_DASH;
  if (cents < 100) return `$0.${String(cents).padStart(2, '0')}`;
  const dollars = Math.floor(cents / 100);
  const rem = cents % 100;
  return `$${dollars}.${String(rem).padStart(2, '0')}`;
}

function formatAvg(ms: number | null): string {
  if (ms === null || ms === undefined) return EM_DASH;
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

const TREND_ARROW = {
  up: '↑', down: '↓', flat: '·', unknown: '',
} as const;

export function MetricsRow({ metrics }: { metrics: MetricsToday }) {
  const errorCls = metrics.errors > 0
    ? 'text-red-600 dark:text-red-400 font-semibold'
    : 'text-[var(--text-secondary)]';
  return (
    <div
      data-testid="metrics-row"
      className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] font-mono text-[var(--text-secondary)]"
    >
      <span className="text-emerald-600">✓ {metrics.runs_ok}</span>
      <span className={errorCls}>⚠ {metrics.errors}</span>
      <span>
        ⏱ {formatAvg(metrics.avg_duration_ms)} {TREND_ARROW[metrics.avg_duration_trend]}
      </span>
      <span>{formatCost(metrics.token_cost_cents)}</span>
    </div>
  );
}
