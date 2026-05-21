/**
 * Input — Nordic archive style
 * Flat 1px-ruled input, underline-on-focus, no color glow.
 */

import { forwardRef, type InputHTMLAttributes } from 'react';
import { cn } from '@/lib/utils';

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  error?: boolean;
  icon?: React.ReactNode;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ className, error = false, icon, type = 'text', ...props }, ref) => {
    return (
      <div className="relative">
        {icon && (
          <div className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-tertiary)] pointer-events-none">
            {icon}
          </div>
        )}
        <input
          type={type}
          ref={ref}
          className={cn(
            'w-full',
            'bg-[color:var(--nm-paper-warm)]',
            'border border-[color:var(--nm-hairline)]',
            'rounded-[var(--radius-sm)]',
            'px-3 py-2',
            'text-sm text-[var(--nm-ink)]',
            'placeholder:text-[color:var(--nm-ink30)] placeholder:font-normal',
            'font-[family-name:var(--font-sans)]',
            'transition-colors duration-150',

            'focus:outline-none',
            'focus:border-[var(--nm-ink)]',
            'focus:[box-shadow:inset_0_-2px_0_0_var(--nm-ink)]',

            'hover:border-[color:var(--border-strong)]',

            'disabled:opacity-50 disabled:cursor-not-allowed',

            error && [
              'border-[color:var(--color-error)]',
              'focus:border-[color:var(--color-error)]',
              'focus:[box-shadow:inset_0_-2px_0_0_var(--color-error)]',
            ],

            icon && 'pl-9',

            className
          )}
          {...props}
        />
      </div>
    );
  }
);

Input.displayName = 'Input';
