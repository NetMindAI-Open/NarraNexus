/**
 * @file_name: RunStatChips.tsx
 * @author: Bin Liang
 * @date: 2026-08-28
 * @description: The run-stats pill row for one turn — state, duration, cost,
 * tokens, models.
 *
 * Extracted from InnerThoughtCard when the Conversation view needed the same
 * row: both surfaces answer "what did this one turn cost", and two copies of
 * that answer drift silently (the same turn reading 2.4M on one screen and
 * 2.40M on the other, with no way to tell which is rounded — the exact failure
 * lib/tokenFormat.ts was created to end).
 *
 * Extraction settled which rules win: `lib/tokenFormat` for tokens and USD.
 * That changes two things versus the card's private copies — M-scale counts now
 * carry two decimals, and a sub-hundredth-of-a-cent turn renders "<$0.0001"
 * instead of a bare "$0" that reads as free. `formatDuration` had no shared
 * twin and lives in `lib/runStats` alongside the `hasRunStats` predicate, so
 * this file can export components only (react-refresh).
 *
 * Every chip renders only when its datum exists: legacy rows (no lifecycle
 * columns) and cost-less turns collapse to whatever is actually known, and
 * `hasRunStats` returns false so the caller can drop the row entirely rather
 * than leave an empty strip.
 */

import { Clock, Coins, Cpu, ArrowDownToLine, AlertTriangle } from 'lucide-react';
import { formatCost, formatTokens, inputSideTokens } from '@/lib/tokenFormat';
import { formatDuration, hasCostToShow, hasRunStats, hasTokens } from '@/lib/runStats';
import type { EventLogMeta } from '@/types/api';

/** One pill in the run-stats row. */
export function StatChip({ icon, children, title }: {
  icon: React.ReactNode;
  children: React.ReactNode;
  title?: string;
}) {
  return (
    <span
      title={title}
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-[family-name:var(--font-mono,monospace)]"
      style={{
        color: 'var(--text-secondary)',
        background: 'var(--bg-tertiary, rgba(0,0,0,0.04))',
        border: '1px solid var(--border-subtle)',
      }}
    >
      {icon}
      {children}
    </span>
  );
}

export function RunStatChips({ meta, t }: { meta: EventLogMeta; t: (k: string) => string }) {
  if (!hasRunStats(meta)) return null;

  const failed = meta.state === 'failed';
  const cancelled = meta.state === 'cancelled';
  // Shared with the cost popover and the account page's usage section: the
  // three-input-bucket rule is one rule, and it has already shipped a wrong
  // number once (see lib/tokenFormat.ts).
  const inputSide = inputSideTokens(meta);

  return (
    <div className="flex flex-wrap items-center gap-1.5" data-testid="run-stat-chips">
      {(failed || cancelled) && (
        <span
          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold"
          style={{
            color: failed ? 'var(--color-error)' : 'var(--text-secondary)',
            background: failed ? 'rgba(192,57,43,0.08)' : 'var(--bg-tertiary, rgba(0,0,0,0.04))',
            border: `1px solid ${failed ? 'color-mix(in srgb, var(--color-error) 25%, transparent)' : 'var(--border-subtle)'}`,
          }}
        >
          <AlertTriangle className="w-2.5 h-2.5" />
          {t(failed ? 'chat.inner.meta.stateFailed' : 'chat.inner.meta.stateCancelled')}
        </span>
      )}
      {meta.duration_seconds != null && (
        <StatChip icon={<Clock className="w-2.5 h-2.5" />} title={t('chat.inner.meta.duration')}>
          {formatDuration(meta.duration_seconds)}
        </StatChip>
      )}
      {hasCostToShow(meta) && (
        <StatChip icon={<Coins className="w-2.5 h-2.5" />} title={t('chat.inner.meta.cost')}>
          {formatCost(meta.total_cost_usd)}
        </StatChip>
      )}
      {hasTokens(meta) && (
        <StatChip
          icon={<ArrowDownToLine className="w-2.5 h-2.5" />}
          title={t('chat.inner.meta.tokens')}
        >
          {formatTokens(inputSide)} / {formatTokens(meta.output_tokens)}
        </StatChip>
      )}
      {meta.models.map((m) => (
        <StatChip key={m} icon={<Cpu className="w-2.5 h-2.5" />} title={t('chat.inner.meta.model')}>
          {m}
        </StatChip>
      ))}
    </div>
  );
}
