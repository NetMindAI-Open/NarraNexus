/**
 * KPICard - Reusable metric display card
 *
 * Used across AwarenessPanel and JobsPanel for
 * displaying key performance indicators.
 */

import { cn } from '@/lib/utils';

const colorMap = {
  accent: {
    bg: 'bg-[var(--accent-glow)]',
    icon: 'text-[var(--accent-primary)]',
    value: 'text-[var(--accent-primary)]',
    glow: 'shadow-[0_0_15px_var(--accent-glow)]',
  },
  success: {
    bg: 'bg-[var(--color-success)]/10',
    icon: 'text-[var(--color-success)]',
    value: 'text-[var(--color-success)]',
    glow: 'shadow-[0_0_15px_rgba(34,197,94,0.2)]',
  },
  warning: {
    bg: 'bg-[var(--color-warning)]/10',
    icon: 'text-[var(--color-warning)]',
    value: 'text-[var(--color-warning)]',
    glow: 'shadow-[0_0_15px_rgba(234,179,8,0.2)]',
  },
  error: {
    bg: 'bg-[var(--color-error)]/10',
    icon: 'text-[var(--color-error)]',
    value: 'text-[var(--color-error)]',
    glow: 'shadow-[0_0_15px_rgba(239,68,68,0.2)]',
  },
  secondary: {
    bg: 'bg-[var(--accent-secondary)]/10',
    icon: 'text-[var(--accent-secondary)]',
    value: 'text-[var(--accent-secondary)]',
    glow: 'shadow-[0_0_15px_rgba(192,132,252,0.2)]',
  },
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
    <div
      className={cn(
        'p-2.5 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)]',
        'transition-all duration-300 hover:border-[var(--accent-primary)]/30',
        pulse && colors.glow
      )}
    >
      <div className="flex items-center gap-2 mb-1.5">
        <div className={cn('w-6 h-6 rounded-lg flex items-center justify-center', colors.bg)}>
          <Icon className={cn('w-3 h-3', colors.icon, pulse && 'animate-pulse')} />
        </div>
        <span className="text-[9px] text-[var(--text-tertiary)] uppercase tracking-wider font-medium">{label}</span>
      </div>
      <div className={cn('text-lg font-bold font-mono', colors.value)}>{value}</div>
      {subtext && <div className="text-[8px] text-[var(--text-tertiary)] mt-0.5 font-mono truncate">{subtext}</div>}
    </div>
  );
}
