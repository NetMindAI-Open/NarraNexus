/**
 * Button — Nordic archive style
 * Flat right-angle rectangles, 1px rules, DM Mono labels on small sizes.
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
          'transition-colors duration-150 ease-out',
          'disabled:opacity-40 disabled:cursor-not-allowed disabled:pointer-events-none',
          'focus-visible:outline focus-visible:outline-1 focus-visible:outline-[var(--text-primary)] focus-visible:outline-offset-2',
          'select-none tracking-tight',

          // Variants
          variant === 'default' && [
            'bg-[var(--bg-elevated)] text-[var(--text-primary)]',
            'border border-[var(--border-default)]',
            'hover:bg-[var(--bg-secondary)] hover:border-[var(--border-strong)]',
            'active:bg-[var(--bg-tertiary)]',
          ],
          variant === 'ghost' && [
            'bg-transparent text-[var(--text-secondary)]',
            'border border-transparent',
            'hover:bg-[var(--bg-secondary)] hover:text-[var(--text-primary)]',
            'active:bg-[var(--bg-tertiary)]',
          ],
          variant === 'outline' && [
            'bg-transparent text-[var(--text-primary)]',
            'border border-[var(--border-strong)]',
            'hover:bg-[var(--text-primary)] hover:text-[var(--text-inverse)]',
            'hover:border-[var(--text-primary)]',
          ],
          variant === 'accent' && [
            // Auto-inverts via theme: ink-black in light mode, paper-white in dark mode.
            'bg-[var(--text-primary)] text-[var(--text-inverse)]',
            'border border-[var(--text-primary)]',
            'hover:opacity-90',
            'active:opacity-80',
          ],
          variant === 'danger' && [
            'bg-transparent text-[var(--color-red-500)]',
            'border border-[var(--color-red-500)]',
            'hover:bg-[var(--color-red-500)] hover:text-[#ffffff]',
          ],

          // Sizes — DM Mono archive labels on sm/icon, Barlow elsewhere
          size === 'sm' && 'h-8 px-3 text-[11px] uppercase tracking-[0.12em] font-[family-name:var(--font-mono)]',
          size === 'md' && 'h-10 px-4 text-sm',
          size === 'lg' && 'h-12 px-6 text-base',
          size === 'icon' && 'h-9 w-9',

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
