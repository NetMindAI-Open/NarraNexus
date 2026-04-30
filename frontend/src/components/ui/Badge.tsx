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
          'font-[family-name:var(--font-mono)] font-normal',
          'uppercase tracking-[0.12em]',
          'whitespace-nowrap',
          'transition-colors duration-150',

          variant === 'default' && [
            'bg-transparent',
            'text-[var(--text-secondary)]',
            'border border-[var(--border-subtle)]',
          ],
          variant === 'accent' && [
            'bg-[var(--text-primary)]',
            'text-[var(--text-inverse)]',
            'border border-[var(--text-primary)]',
          ],
          variant === 'success' && [
            'bg-transparent',
            'text-[var(--color-green-500)]',
            'border border-[var(--color-green-500)]',
          ],
          variant === 'warning' && [
            'bg-transparent',
            'text-[var(--color-yellow-500)]',
            'border border-[var(--color-yellow-500)]',
          ],
          variant === 'error' && [
            'bg-transparent',
            'text-[var(--color-red-500)]',
            'border border-[var(--color-red-500)]',
          ],
          variant === 'outline' && [
            'bg-transparent',
            'text-[var(--text-secondary)]',
            'border border-[var(--border-strong)]',
          ],

          size === 'sm' && 'h-5 px-1.5 text-[9px]',
          size === 'md' && 'h-6 px-2 text-[10px]',
          size === 'lg' && 'h-7 px-2.5 text-[11px]',

          className
        )}
        style={{ borderRadius: 0 }}
        {...props}
      >
        {pulse && (
          <span
            className={cn(
              'h-1.5 w-1.5',
              variant === 'accent' && 'bg-[var(--text-inverse)]',
              variant === 'success' && 'bg-[var(--color-green-500)]',
              variant === 'warning' && 'bg-[var(--color-yellow-500)]',
              variant === 'error' && 'bg-[var(--color-red-500)]',
              variant === 'default' && 'bg-[var(--text-tertiary)]',
              variant === 'outline' && 'bg-[var(--text-tertiary)]',
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
