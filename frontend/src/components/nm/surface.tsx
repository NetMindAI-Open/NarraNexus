/**
 * @file_name: surface.tsx
 * @author: NM Design System Phase 1 (M2)
 * @date: 2026-05-18
 * @description: Surface primitives — PaperCard, RaisedPanel, SunkenWell, Divider.
 * Implements NM Axiom #4 (lift via paper, not shadow) — three semantic surface
 * elevations that all stay within the warm paper family.
 *
 */

import { forwardRef, type HTMLAttributes, type ReactNode } from 'react';
import { cn } from '@/lib/utils';

export type NMPadding = 'none' | 'sm' | 'md' | 'lg';

const PAD_CLASS: Record<NMPadding, string> = {
  none: 'p-0',
  sm: 'p-3',
  md: 'p-4',
  lg: 'p-6',
};

// ---------------------------------------------------------------------------
// PaperCard — base card on warm paper
// ---------------------------------------------------------------------------
export interface PaperCardProps extends HTMLAttributes<HTMLDivElement> {
  padding?: NMPadding;
  children: ReactNode;
}

export const PaperCard = forwardRef<HTMLDivElement, PaperCardProps>(
  ({ padding = 'md', className, children, ...rest }, ref) => {
    return (
      <div
        ref={ref}
        data-nm="paper-card"
        className={cn(
          'rounded-[var(--radius-md)] border border-[color:var(--nm-hairline)] bg-[color:var(--nm-card)]',
          'transition-[border-color,background-color] duration-150',
          PAD_CLASS[padding],
          className
        )}
        {...rest}
      >
        {children}
      </div>
    );
  }
);
PaperCard.displayName = 'PaperCard';

// ---------------------------------------------------------------------------
// RaisedPanel — sits "above" the page (dropdown/popover/tooltip content)
// ---------------------------------------------------------------------------
export interface RaisedPanelProps extends HTMLAttributes<HTMLDivElement> {
  padding?: NMPadding;
  children: ReactNode;
}

export const RaisedPanel = forwardRef<HTMLDivElement, RaisedPanelProps>(
  ({ padding = 'md', className, children, ...rest }, ref) => {
    return (
      <div
        ref={ref}
        data-nm="raised-panel"
        className={cn(
          'rounded-[var(--radius-md)] border border-[color:var(--nm-hairline)] bg-[color:var(--nm-raised)]',
          PAD_CLASS[padding],
          className
        )}
        style={{
          boxShadow: 'var(--nm-elev-1)',
          ...rest.style,
        }}
        {...rest}
      >
        {children}
      </div>
    );
  }
);
RaisedPanel.displayName = 'RaisedPanel';

// ---------------------------------------------------------------------------
// SunkenWell — "depressed" surface (inputs, code, quoted content)
// ---------------------------------------------------------------------------
export interface SunkenWellProps extends HTMLAttributes<HTMLDivElement> {
  padding?: NMPadding;
  children: ReactNode;
}

export const SunkenWell = forwardRef<HTMLDivElement, SunkenWellProps>(
  ({ padding = 'md', className, children, ...rest }, ref) => {
    return (
      <div
        ref={ref}
        data-nm="sunken-well"
        className={cn(
          'rounded-[var(--radius-sm)] bg-[color:var(--nm-paper-warm)]',
          PAD_CLASS[padding],
          className
        )}
        style={{
          boxShadow: 'inset 0 0 0 1px var(--nm-hairline)',
          ...rest.style,
        }}
        {...rest}
      >
        {children}
      </div>
    );
  }
);
SunkenWell.displayName = 'SunkenWell';

// ---------------------------------------------------------------------------
// Divider — horizontal/vertical separator with thick variant
// ---------------------------------------------------------------------------
export interface DividerProps extends HTMLAttributes<HTMLHRElement> {
  variant?: 'default' | 'thick';
  orientation?: 'horizontal' | 'vertical';
}

export function Divider({
  variant = 'default',
  orientation = 'horizontal',
  className,
  ...rest
}: DividerProps) {
  const thick = variant === 'thick';
  if (orientation === 'vertical') {
    return (
      <div
        data-nm="divider"
        data-variant={variant}
        data-orientation="vertical"
        role="separator"
        aria-orientation="vertical"
        className={cn('inline-block self-stretch', className)}
        style={{
          width: thick ? 2 : 1,
          background: thick ? 'var(--nm-ink)' : 'var(--nm-hairline)',
        }}
        {...(rest as HTMLAttributes<HTMLDivElement>)}
      />
    );
  }
  return (
    <hr
      data-nm="divider"
      data-variant={variant}
      data-orientation="horizontal"
      className={cn('w-full border-0', className)}
      style={{
        height: thick ? 2 : 1,
        background: thick ? 'var(--nm-ink)' : 'var(--nm-hairline)',
        margin: thick ? '1.5rem 0' : '1rem 0',
      }}
      {...rest}
    />
  );
}
