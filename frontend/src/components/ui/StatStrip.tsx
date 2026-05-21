/**
 * StatStrip — NM Design System (M3 Wave 4)
 *
 * Single horizontal row of numeric stats divided by hairline separators.
 * API preserved (label, value, icon, tone, subtext, pulse) for existing
 * callers; internals retargeted to NM tokens + PaperCard surface.
 */

import { cn } from '@/lib/utils';
import { PaperCard } from '@/components/nm';

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
  default:   'text-[var(--nm-ink)]',
  accent:    'text-[var(--nm-ink)]',
  success:   'text-[var(--color-success)]',
  warning:   'text-[var(--color-warning)]',
  error:     'text-[var(--color-error)]',
  secondary: 'text-[var(--nm-ink70)]',
};

interface StatStripProps {
  items: StatItem[];
  className?: string;
}

export function StatStrip({ items, className }: StatStripProps) {
  return (
    <PaperCard padding="none" className={cn('flex items-stretch', className)}>
      {items.map((item, i) => {
        const Icon = item.icon;
        return (
          <div
            key={item.label + i}
            className={cn(
              'flex-1 min-w-0 px-4 py-3',
              i > 0 && 'border-l border-[color:var(--nm-hairline)]'
            )}
          >
            <div
              className="flex items-center gap-1.5 mb-1.5 text-[10px] uppercase tracking-[0.12em]"
              style={{ fontFamily: 'var(--font-mono)', color: 'var(--nm-ink50)' }}
            >
              {Icon && (
                <Icon className={cn('w-3 h-3 shrink-0', item.pulse && 'animate-pulse')} />
              )}
              <span className="truncate">{item.label}</span>
            </div>
            <div
              className={cn(
                'font-bold text-xl leading-none tracking-tight',
                toneText[item.tone ?? 'default']
              )}
              style={{
                fontFamily: 'var(--font-display)',
                fontVariantNumeric: 'tabular-nums',
              }}
            >
              {item.value}
            </div>
            {item.subtext && (
              <div
                className="mt-1 text-[10px] truncate"
                style={{ fontFamily: 'var(--font-mono)', color: 'var(--nm-ink50)' }}
              >
                {item.subtext}
              </div>
            )}
          </div>
        );
      })}
    </PaperCard>
  );
}
