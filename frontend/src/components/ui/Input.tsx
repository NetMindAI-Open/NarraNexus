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
            'bg-[var(--bg-primary)]',
            'border border-[var(--rule)]',
            'px-3 py-2',
            'text-sm text-[var(--text-primary)]',
            'placeholder:text-[var(--text-tertiary)] placeholder:font-light',
            'font-[family-name:var(--font-sans)]',
            'transition-colors duration-150',

            'focus:outline-none',
            'focus:border-[var(--text-primary)]',
            'focus:bg-[var(--bg-primary)]',

            'hover:border-[var(--border-strong)]',

            'disabled:opacity-50 disabled:cursor-not-allowed disabled:bg-[var(--bg-secondary)]',

            error && [
              'border-[var(--color-red-500)]',
              'focus:border-[var(--color-red-500)]',
            ],

            icon && 'pl-9',

            className
          )}
          style={{ borderRadius: 0 }}
          {...props}
        />
      </div>
    );
  }
);

Input.displayName = 'Input';
