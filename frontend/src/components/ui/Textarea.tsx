/**
 * Textarea — Nordic archive style
 * Flat 1px-ruled surface, no glow, no decoration.
 */

import { forwardRef, type TextareaHTMLAttributes } from 'react';
import { cn } from '@/lib/utils';

interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  error?: boolean;
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, error = false, ...props }, ref) => {
    return (
      <div className="relative">
        <textarea
          ref={ref}
          className={cn(
            'w-full',
            'bg-[var(--bg-primary)]',
            'border border-[var(--rule)]',
            'px-3 py-2.5',
            'text-sm text-[var(--text-primary)]',
            'placeholder:text-[var(--text-tertiary)] placeholder:font-light',
            'font-[family-name:var(--font-sans)]',
            'transition-colors duration-150',
            'resize-none min-h-[100px]',

            'focus:outline-none',
            'focus:border-[var(--text-primary)]',

            'hover:border-[var(--border-strong)]',

            'disabled:opacity-50 disabled:cursor-not-allowed disabled:bg-[var(--bg-secondary)]',

            error && [
              'border-[var(--color-red-500)]',
              'focus:border-[var(--color-red-500)]',
            ],

            className
          )}
          style={{ borderRadius: 0 }}
          {...props}
        />
      </div>
    );
  }
);

Textarea.displayName = 'Textarea';
