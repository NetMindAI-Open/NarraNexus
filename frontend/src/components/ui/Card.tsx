/**
 * Card — Nordic archive style
 *
 * Default is a "section": NO outer border. Sections inside a panel are
 * separated from each other by a single hairline rule on the CardHeader,
 * not by boxing every section in its own frame. This removes the
 * "nested boxes" visual noise the archive aesthetic rejects.
 *
 * Use `variant="bordered"` only for genuinely free-floating items
 * (standalone dialogs, login card).
 */

import { forwardRef, type HTMLAttributes } from 'react';
import { cn } from '@/lib/utils';

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  variant?: 'default' | 'bordered' | 'glass' | 'elevated' | 'sunken';
  glow?: boolean;
  noPadding?: boolean;
}

export const Card = forwardRef<HTMLDivElement, CardProps>(
  ({ className, variant = 'default', children, ...props }, ref) => {
    return (
      <div
        ref={ref}
        className={cn(
          'relative',

          // Default = no visible border, just a semantic region.
          // Use this for every top-level panel that lives inside MainLayout.
          variant === 'default' && 'bg-[var(--bg-primary)]',

          // Explicit bordered variant for cards that float on their own
          variant === 'bordered' && [
            'bg-[var(--bg-primary)]',
            'border border-[var(--rule)]',
          ],
          // Kept for back-compat; behaves like bordered without blur
          variant === 'glass' && [
            'bg-[var(--bg-primary)]',
            'border border-[var(--glass-border)]',
          ],
          variant === 'elevated' && [
            'bg-[var(--bg-elevated)]',
            'border border-[var(--rule)]',
          ],
          variant === 'sunken' && [
            'bg-[var(--bg-secondary)]',
          ],

          className
        )}
        {...props}
      >
        {children}
      </div>
    );
  }
);

Card.displayName = 'Card';

/**
 * CardHeader — flat header with DM Mono label and an under-rule.
 *
 * Replaces the old "mini icon tile + title + badge" layout.
 * A single bottom hairline separates header from content; the panel
 * itself no longer needs a surrounding frame.
 */
export const CardHeader = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        'px-5',
        'border-b border-[var(--rule)]',
        'flex items-center justify-between gap-3',
        'h-[52px] shrink-0',
        className
      )}
      {...props}
    />
  )
);
CardHeader.displayName = 'CardHeader';

export const CardContent = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn('p-5', className)} {...props} />
  )
);
CardContent.displayName = 'CardContent';

/**
 * CardTitle — DM Mono uppercase label.
 *
 * Sized so it reads at a glance from a full-width desktop view without
 * overwhelming the content below. Icon is tuned to match x-height of the
 * label (icon ~1.2× x-height) so they feel "set" together, not stacked.
 *
 * Consumers can pass: <CardTitle><Brain /> Context</CardTitle> — the
 * icon sizing + color is standardised via [&>svg] rules, no need to
 * repeat w-3.5 className at every call site.
 */
export const CardTitle = forwardRef<HTMLHeadingElement, HTMLAttributes<HTMLHeadingElement>>(
  ({ className, ...props }, ref) => (
    <h3
      ref={ref}
      className={cn(
        'flex items-center gap-2',
        'text-[13px] font-medium uppercase leading-none',
        'font-[family-name:var(--font-mono)]',
        'tracking-[0.14em]',
        'text-[var(--text-primary)]',
        // Direct-child SVGs only — nested icons inside children (like counters)
        // set their own size.
        '[&>svg]:w-[15px] [&>svg]:h-[15px] [&>svg]:text-[var(--text-secondary)] [&>svg]:shrink-0',
        className
      )}
      {...props}
    />
  )
);
CardTitle.displayName = 'CardTitle';

export const CardFooter = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        'px-5 py-3',
        'border-t border-[var(--rule)]',
        className
      )}
      {...props}
    />
  )
);
CardFooter.displayName = 'CardFooter';
