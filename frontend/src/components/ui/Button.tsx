/**
 * Button — NM Design System (M3 Wave 5)
 *
 * In-place restyle of the existing UI Button. API preserved (variant,
 * size, icon, glow) so every caller across the app keeps working.
 * Internals use NM tokens + radius-sm + paper-warm hover + warm oxblood
 * danger (per Axiom #1 — NEVER Carbon orange for destructive).
 */

import { forwardRef, type ButtonHTMLAttributes } from 'react';
import { cn } from '@/lib/utils';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'default' | 'ghost' | 'outline' | 'accent' | 'danger';
  size?: 'sm' | 'md' | 'lg' | 'icon';
  glow?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = 'default', size = 'md', children, ...props }, ref) => {
    return (
      <button
        ref={ref}
        className={cn(
          // Base
          'relative inline-flex items-center justify-center gap-2',
          'font-[family-name:var(--font-sans)] font-medium',
          'rounded-[var(--radius-sm)]',
          'transition-colors duration-150 ease-out',
          'disabled:opacity-50 disabled:cursor-not-allowed disabled:pointer-events-none',
          'focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--nm-ink)] focus-visible:outline-offset-2',
          'select-none tracking-tight',

          // Variants — NM tokens
          variant === 'default' && [
            'bg-[var(--nm-raised)] text-[var(--nm-ink)]',
            'border border-[var(--nm-hairline)]',
            'hover:bg-[var(--nm-paper-warm)] hover:border-[color:var(--border-strong)]',
            'active:opacity-90',
          ],
          variant === 'ghost' && [
            'bg-transparent text-[var(--nm-ink)]',
            'border border-transparent',
            'hover:bg-[var(--nm-paper-warm)]',
          ],
          variant === 'outline' && [
            'bg-transparent text-[var(--nm-ink)]',
            'border border-[var(--nm-ink)]',
            'hover:bg-[var(--nm-ink)] hover:text-[var(--nm-paper)]',
          ],
          variant === 'accent' && [
            // Primary action — ink fill (Axiom #3 allowed exception)
            'bg-[var(--nm-ink)] text-[var(--nm-paper)]',
            'border border-[var(--nm-ink)]',
            'hover:opacity-90 active:opacity-80',
          ],
          variant === 'danger' && [
            // Warm oxblood (Axiom #2), NEVER Carbon (Axiom #1)
            'bg-[var(--color-error)] text-white',
            'border border-[var(--color-error)]',
            'hover:opacity-90 active:opacity-80',
          ],

          // Sizes
          size === 'sm' && 'h-8 px-3 text-[11px] uppercase tracking-[0.10em] font-[family-name:var(--font-mono)]',
          size === 'md' && 'h-10 px-4 text-sm',
          size === 'lg' && 'h-12 px-6 text-base',
          size === 'icon' && 'h-9 w-9 rounded-[var(--radius-sm)]',

          className
        )}
        {...props}
      >
        {children}
      </button>
    );
  }
);

Button.displayName = 'Button';
