/**
 * @file_name: viz.tsx
 * @author: NM Design System Phase 1 (M2)
 * @date: 2026-05-18
 * @description: Data viz primitives — KPITile, StatStrip, ChartCard.
 * Wrappers that wire NM tokens to data display surfaces.
 *
 * Spec: reference/self_notebook/specs/2026-05-18-nm-design-system-design.md §5.11
 */

import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { PaperCard } from './surface';
import { BracketSectionLabel } from './bracket';

// ---------------------------------------------------------------------------
// KPITile — display number + mono label + optional trend arrow
// ---------------------------------------------------------------------------
export interface KPITileProps {
  label: string;
  value: ReactNode;
  /** Optional trend percent (positive or negative); used to color arrow */
  trend?: number;
  /** Override trend direction (otherwise inferred from sign of trend) */
  trendDir?: 'up' | 'down' | 'flat';
  /** When true, an UP trend is good (default). When false (e.g., cost), UP is warning. */
  upIsGood?: boolean;
  className?: string;
}

export function KPITile({
  label,
  value,
  trend,
  trendDir,
  upIsGood = true,
  className,
}: KPITileProps) {
  const dir = trendDir ?? (trend === undefined ? 'flat' : trend > 0 ? 'up' : trend < 0 ? 'down' : 'flat');
  const isPositiveOutcome = dir === 'flat' ? null : dir === 'up' ? upIsGood : !upIsGood;
  const trendColor =
    isPositiveOutcome === null
      ? 'var(--nm-ink50)'
      : isPositiveOutcome
      ? 'var(--color-success)'
      : 'var(--color-error)';
  return (
    <PaperCard padding="md" data-nm="kpi-tile" className={cn('flex flex-col gap-1', className)}>
      <div
        className="text-[11px] uppercase tracking-[0.10em]"
        style={{ fontFamily: 'var(--font-mono)', color: 'var(--nm-ink50)' }}
      >
        {label}
      </div>
      <div
        className="text-3xl font-bold leading-tight"
        style={{ color: 'var(--nm-ink)', fontFamily: 'var(--font-display)', fontVariantNumeric: 'tabular-nums' }}
      >
        {value}
      </div>
      {trend !== undefined && (
        <div className="text-xs inline-flex items-center gap-1" style={{ color: trendColor, fontFamily: 'var(--font-mono)' }}>
          <span aria-hidden="true">
            {dir === 'up' ? '↑' : dir === 'down' ? '↓' : '·'}
          </span>
          <span>{Math.abs(trend).toFixed(1)}%</span>
        </div>
      )}
    </PaperCard>
  );
}

// ---------------------------------------------------------------------------
// StatStrip — horizontal KPI strip with hairline separators
// ---------------------------------------------------------------------------
export interface StatStripItem {
  label: string;
  value: ReactNode;
  trend?: number;
}

export interface StatStripProps {
  items: StatStripItem[];
  className?: string;
}

export function StatStrip({ items, className }: StatStripProps) {
  return (
    <PaperCard
      padding="none"
      data-nm="stat-strip"
      className={cn('flex flex-row divide-x', className)}
    >
      {items.map((it, i) => (
        <div
          key={i}
          className="flex-1 p-4 flex flex-col gap-1"
          style={{ borderColor: 'var(--nm-hairline)' }}
        >
          <div
            className="text-[10px] uppercase tracking-wider"
            style={{ fontFamily: 'var(--font-mono)', color: 'var(--nm-ink50)' }}
          >
            {it.label}
          </div>
          <div
            className="text-xl font-bold"
            style={{ color: 'var(--nm-ink)', fontFamily: 'var(--font-display)', fontVariantNumeric: 'tabular-nums' }}
          >
            {it.value}
          </div>
        </div>
      ))}
    </PaperCard>
  );
}

// ---------------------------------------------------------------------------
// ChartCard — PaperCard + BracketSectionLabel + content area for chart
// ---------------------------------------------------------------------------
export interface ChartCardProps {
  title: string;
  subtitle?: ReactNode;
  /** Right-aligned actions (e.g., date range select, IconButton) */
  actions?: ReactNode;
  /** Min height of chart canvas region */
  minHeight?: number;
  children: ReactNode;
  className?: string;
}

export function ChartCard({
  title,
  subtitle,
  actions,
  minHeight = 240,
  children,
  className,
}: ChartCardProps) {
  return (
    <PaperCard data-nm="chart-card" padding="md" className={cn('flex flex-col gap-3', className)}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <BracketSectionLabel>{title}</BracketSectionLabel>
          {subtitle && (
            <p
              className="mt-1 text-xs"
              style={{ color: 'var(--nm-ink70)' }}
            >
              {subtitle}
            </p>
          )}
        </div>
        {actions && <div className="shrink-0">{actions}</div>}
      </div>
      <div style={{ minHeight }} className="w-full">
        {children}
      </div>
    </PaperCard>
  );
}
