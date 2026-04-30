/**
 * @file_name: MetricsRow.tsx
 * @description: v2.3 — labeled stat strip. Every number is paired with a
 * lowercase label so users can scan without decoding icons. Missing data
 * (token_cost_cents) renders as em-dash, not 0.
 */
import type { MetricsToday } from '@/types';

const EM_DASH = '—';

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
  up: '↑',
  down: '↓',
  flat: '·',
  unknown: '',
} as const;

const TREND_TITLE = {
  up: 'slower than yesterday',
  down: 'faster than yesterday',
  flat: 'same as yesterday',
  unknown: '',
} as const;

function Stat({
  label,
  value,
  tone = 'default',
  hint,
}: {
  label: string;
  value: React.ReactNode;
  tone?: 'default' | 'success' | 'danger';
  hint?: string;
}) {
  const valueCls =
    tone === 'success'
      ? 'text-[var(--color-green-500)]'
      : tone === 'danger'
        ? 'text-[var(--color-red-500)]'
        : 'text-[var(--text-primary)]';
  return (
    <span
      className="inline-flex items-baseline gap-1 tabular-nums"
      title={hint}
    >
      <span className={`text-[12px] font-medium ${valueCls}`}>{value}</span>
      <span className="text-[10px] uppercase tracking-[0.08em] text-[var(--text-tertiary)] font-[family-name:var(--font-mono)]">
        {label}
      </span>
    </span>
  );
}

export function MetricsRow({ metrics }: { metrics: MetricsToday }) {
  return (
    <div
      data-testid="metrics-row"
      className="flex flex-wrap items-baseline gap-x-4 gap-y-1"
    >
      <Stat label="ok" value={metrics.runs_ok} tone="success" hint="Runs completed today" />
      <Stat
        label="errors"
        value={metrics.errors}
        tone={metrics.errors > 0 ? 'danger' : 'default'}
        hint="Errors today"
      />
      <Stat
        label="avg"
        value={
          <>
            {formatAvg(metrics.avg_duration_ms)}
            {metrics.avg_duration_trend !== 'unknown' && (
              <span
                className="ml-0.5 text-[var(--text-tertiary)]"
                title={TREND_TITLE[metrics.avg_duration_trend]}
              >
                {TREND_ARROW[metrics.avg_duration_trend]}
              </span>
            )}
          </>
        }
        hint="Average run duration today"
      />
      <Stat label="cost" value={formatCost(metrics.token_cost_cents)} hint="Token spend today" />
    </div>
  );
}
