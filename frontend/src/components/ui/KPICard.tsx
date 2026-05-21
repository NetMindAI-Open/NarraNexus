/**
 * KPICard — NM Design System (M3 Wave 4)
 *
 * Restyled in-place to use NM PaperCard surface + display-font numeric +
 * mono uppercase label. API preserved (icon, color, subtext, pulse) so
 * existing callers in DashboardPage etc. don't need to change.
 */

import { cn } from '@/lib/utils';
import { PaperCard } from '@/components/nm';

const colorMap = {
  accent:    { icon: 'text-[var(--nm-ink)]',          value: 'text-[var(--nm-ink)]' },
  success:   { icon: 'text-[var(--color-success)]',   value: 'text-[var(--color-success)]' },
  warning:   { icon: 'text-[var(--color-warning)]',   value: 'text-[var(--color-warning)]' },
  error:     { icon: 'text-[var(--color-error)]',     value: 'text-[var(--color-error)]' },
  secondary: { icon: 'text-[var(--nm-ink70)]',        value: 'text-[var(--nm-ink70)]' },
};

export type KPIColor = keyof typeof colorMap;

interface KPICardProps {
  label: string;
  value: string | number;
  icon: React.ElementType;
  color?: KPIColor;
  subtext?: string;
  pulse?: boolean;
}

export function KPICard({
  label,
  value,
  icon: Icon,
  color = 'accent',
  subtext,
  pulse,
}: KPICardProps) {
  const colors = colorMap[color];

  return (
    <PaperCard padding="md" className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2">
        <Icon className={cn('w-3.5 h-3.5', colors.icon, pulse && 'animate-pulse')} />
        <span
          className="text-[10px] uppercase tracking-[0.12em]"
          style={{
            fontFamily: 'var(--font-mono)',
            color: 'var(--nm-ink50)',
          }}
        >
          {label}
        </span>
      </div>
      <div
        className={cn(
          'text-2xl font-bold leading-tight tracking-tight',
          colors.value
        )}
        style={{
          fontFamily: 'var(--font-display)',
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {value}
      </div>
      {subtext && (
        <div
          className="text-[10px] truncate"
          style={{
            fontFamily: 'var(--font-mono)',
            color: 'var(--nm-ink50)',
          }}
        >
          {subtext}
        </div>
      )}
    </PaperCard>
  );
}
