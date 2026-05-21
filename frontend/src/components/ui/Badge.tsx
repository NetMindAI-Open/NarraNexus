/**
 * Badge — Nordic archive style
 * Flat rectangle, DM Mono, uppercase, no fill (border only by default).
 */

import { forwardRef, type HTMLAttributes } from 'react';
import { cn } from '@/lib/utils';

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: 'default' | 'accent' | 'success' | 'warning' | 'error' | 'outline';
  size?: 'sm' | 'md' | 'lg';
  pulse?: boolean;
  glow?: boolean;
}

export const Badge = forwardRef<HTMLSpanElement, BadgeProps>(
  ({ className, variant = 'default', size = 'md', pulse = false, children, ...props }, ref) => {
    return (
      <span
        ref={ref}
        className={cn(
          'relative inline-flex items-center justify-center gap-1.5',
          'font-[family-name:var(--font-mono)] font-medium',
          'uppercase tracking-[0.10em]',
          'rounded-[var(--radius-xs)]',
          'whitespace-nowrap',
          'transition-colors duration-150',

          variant === 'default' && [
            'bg-transparent',
            'text-[color:var(--nm-ink70)]',
            'border border-[color:var(--nm-hairline)]',
          ],
          variant === 'accent' && [
            'bg-[color:var(--nm-ink)]',
            'text-[color:var(--nm-paper)]',
            'border border-[color:var(--nm-ink)]',
          ],
          variant === 'success' && [
            'bg-transparent',
            'text-[color:var(--color-success)]',
            'border border-[color:var(--color-success)]',
          ],
          variant === 'warning' && [
            'bg-transparent',
            'text-[color:var(--color-warning)]',
            'border border-[color:var(--color-warning)]',
          ],
          variant === 'error' && [
            'bg-transparent',
            'text-[color:var(--color-error)]',
            'border border-[color:var(--color-error)]',
          ],
          variant === 'outline' && [
            'bg-transparent',
            'text-[color:var(--nm-ink70)]',
            'border border-[color:var(--border-strong)]',
          ],

          size === 'sm' && 'h-5 px-1.5 text-[9px]',
          size === 'md' && 'h-6 px-2 text-[10px]',
          size === 'lg' && 'h-7 px-2.5 text-[11px]',

          className
        )}
        {...props}
      >
        {pulse && (
          <span
            className={cn(
              'h-1.5 w-1.5 rounded-full',
              variant === 'accent' && 'bg-[color:var(--nm-paper)]',
              variant === 'success' && 'bg-[color:var(--color-success)]',
              variant === 'warning' && 'bg-[color:var(--color-warning)]',
              variant === 'error' && 'bg-[color:var(--color-error)]',
              variant === 'default' && 'bg-[color:var(--nm-ink50)]',
              variant === 'outline' && 'bg-[color:var(--nm-ink50)]',
              'animate-pulse'
            )}
          />
        )}
        <span>{children}</span>
      </span>
    );
  }
);

Badge.displayName = 'Badge';
