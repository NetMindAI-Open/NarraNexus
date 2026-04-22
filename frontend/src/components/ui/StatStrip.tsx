/**
 * StatStrip — a single horizontal row of numeric stats.
 *
 * Replaces the grid-of-bordered-KPI-cards pattern: instead of N mini
 * boxes each with their own border, render one row divided by hairline
 * separators. Reads like a newspaper masthead stats line.
 *
 *   Contacts · 12     Chats · 48     Strong · 7
 *
 * Label is DM Mono uppercase; value is Space Grotesk; optional subtext
 * and status tone are supported but kept restrained.
 */

import { cn } from '@/lib/utils';

export type StatTone = 'default' | 'accent' | 'success' | 'warning' | 'error' | 'secondary';

export interface StatItem {
  label: string;
  value: string | number;
  icon?: React.ElementType;
  tone?: StatTone;
  subtext?: string;
  pulse?: boolean;
}

const toneText: Record<StatTone, string> = {
  default:   'text-[var(--text-primary)]',
  accent:    'text-[var(--text-primary)]',
  success:   'text-[var(--color-green-500)]',
  warning:   'text-[var(--color-yellow-500)]',
  error:     'text-[var(--color-red-500)]',
  secondary: 'text-[var(--text-secondary)]',
};

interface StatStripProps {
  items: StatItem[];
  className?: string;
}

export function StatStrip({ items, className }: StatStripProps) {
  return (
    <div
      className={cn(
        'flex items-stretch',
        'border-y border-[var(--rule)]',
        className
      )}
    >
      {items.map((item, i) => {
        const Icon = item.icon;
        return (
          <div
            key={item.label + i}
            className={cn(
              'flex-1 min-w-0 px-4 py-3',
              i > 0 && 'border-l border-[var(--rule)]'
            )}
          >
            <div className="flex items-center gap-1.5 mb-1 text-[10px] uppercase tracking-[0.14em] font-[family-name:var(--font-mono)] text-[var(--text-tertiary)]">
              {Icon && (
                <Icon className={cn('w-3 h-3 shrink-0', item.pulse && 'animate-pulse')} />
              )}
              <span className="truncate">{item.label}</span>
            </div>
            <div
              className={cn(
                'font-[family-name:var(--font-display)] font-semibold text-xl leading-none tracking-tight',
                toneText[item.tone ?? 'default']
              )}
            >
              {item.value}
            </div>
            {item.subtext && (
              <div className="mt-1 text-[10px] font-[family-name:var(--font-mono)] text-[var(--text-tertiary)] truncate">
                {item.subtext}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
