/**
 * KPICard — Nordic archive style
 * Flat rectangle, DM Mono label, Space Grotesk numeric value. No color chip, no glow.
 */

import { cn } from '@/lib/utils';

const colorMap = {
  accent:    { icon: 'text-[var(--text-primary)]', value: 'text-[var(--text-primary)]' },
  success:   { icon: 'text-[var(--color-green-500)]', value: 'text-[var(--color-green-500)]' },
  warning:   { icon: 'text-[var(--color-yellow-500)]', value: 'text-[var(--color-yellow-500)]' },
  error:     { icon: 'text-[var(--color-red-500)]', value: 'text-[var(--color-red-500)]' },
  secondary: { icon: 'text-[var(--text-secondary)]', value: 'text-[var(--text-secondary)]' },
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
        'p-3 border border-[var(--rule)] bg-[var(--bg-primary)]',
        'transition-colors duration-150 hover:border-[var(--border-strong)]'
      )}
      style={{ borderRadius: 0 }}
    >
      <div className="flex items-center gap-2 mb-2">
        <Icon className={cn('w-3.5 h-3.5', colors.icon, pulse && 'animate-pulse')} />
        <span
          className="text-[9px] text-[var(--text-tertiary)] uppercase tracking-[0.14em] font-normal font-[family-name:var(--font-mono)]"
        >
          {label}
        </span>
      </div>
      <div
        className={cn(
          'text-xl font-semibold font-[family-name:var(--font-display)] tracking-tight',
          colors.value
        )}
      >
        {value}
      </div>
      {subtext && (
        <div className="text-[10px] text-[var(--text-tertiary)] mt-1 font-[family-name:var(--font-mono)] truncate">
          {subtext}
        </div>
      )}
    </div>
  );
}
