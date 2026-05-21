/**
 * @file_name: button.tsx
 * @author: NM Design System Phase 1 (M2)
 * @date: 2026-05-18
 * @description: Button primitives — Button (5 variants), IconButton, ButtonGroup,
 * SplitButton. Implements NM Axiom #3 exception (primary = ink fill is allowed)
 * and Axiom #1 (danger = warm oxblood, NEVER Carbon).
 *
 * Spec: reference/self_notebook/specs/2026-05-18-nm-design-system-design.md §5.8
 */

import {
  forwardRef,
  type ButtonHTMLAttributes,
  type ReactNode,
} from 'react';
import { cn } from '@/lib/utils';

export type NMButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'link';
export type NMButtonSize = 'sm' | 'md' | 'lg';

const SIZE_CLASS: Record<NMButtonSize, string> = {
  sm: 'h-8 px-3 text-xs',
  md: 'h-10 px-4 text-sm',
  lg: 'h-12 px-5 text-base',
};

const VARIANT_CLASS: Record<NMButtonVariant, string> = {
  primary:
    'bg-[color:var(--nm-ink)] text-[color:var(--nm-paper)] border border-[color:var(--nm-ink)] hover:opacity-90 active:opacity-80',
  secondary:
    'bg-[color:var(--nm-raised)] text-[color:var(--nm-ink)] border border-[color:var(--nm-ink)] hover:bg-[color:var(--nm-paper-warm)]',
  ghost:
    'bg-transparent text-[color:var(--nm-ink)] border border-transparent hover:bg-[color:var(--nm-paper-warm)]',
  danger:
    'bg-[color:var(--color-error)] text-white border border-[color:var(--color-error)] hover:opacity-90 active:opacity-80',
  link:
    'bg-transparent text-[color:var(--nm-ink)] border-0 underline underline-offset-4 decoration-1 hover:decoration-2 h-auto px-0',
};

// ---------------------------------------------------------------------------
// Button
// ---------------------------------------------------------------------------
export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: NMButtonVariant;
  size?: NMButtonSize;
  /** Optional leading icon node */
  leading?: ReactNode;
  /** Optional trailing icon node */
  trailing?: ReactNode;
  /** When true, replace content with a small inline spinner + keep label */
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      variant = 'primary',
      size = 'md',
      leading,
      trailing,
      loading,
      disabled,
      className,
      children,
      ...rest
    },
    ref
  ) => {
    const sizeClass = variant === 'link' ? '' : SIZE_CLASS[size];
    return (
      <button
        ref={ref}
        type={rest.type ?? 'button'}
        data-nm="button"
        data-variant={variant}
        data-size={size}
        data-loading={loading ? 'true' : 'false'}
        disabled={disabled || loading}
        className={cn(
          'inline-flex items-center justify-center gap-2 rounded-[var(--radius-sm)] font-medium',
          'transition-colors duration-150',
          'disabled:opacity-50 disabled:cursor-not-allowed',
          'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--nm-ink)]',
          sizeClass,
          VARIANT_CLASS[variant],
          className
        )}
        {...rest}
      >
        {loading ? (
          <span
            data-nm="inline-spinner"
            aria-hidden="true"
            className="inline-block w-3.5 h-3.5 rounded-full border-[1.5px] border-current border-r-transparent animate-spin"
          />
        ) : (
          leading
        )}
        <span>{children}</span>
        {!loading && trailing}
      </button>
    );
  }
);
Button.displayName = 'Button';

// ---------------------------------------------------------------------------
// IconButton — circular hairline + icon center
// ---------------------------------------------------------------------------
export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Accessible label (required — icon-only buttons need this for SR users) */
  label: string;
  size?: NMButtonSize;
  /** Visual style: 'plain' (no border), 'ring' (hairline border) */
  appearance?: 'plain' | 'ring';
  children: ReactNode;
}

const ICON_BTN_SIZE: Record<NMButtonSize, string> = {
  sm: 'w-8 h-8',
  md: 'w-10 h-10',
  lg: 'w-12 h-12',
};

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(
  (
    { label, size = 'md', appearance = 'ring', className, children, ...rest },
    ref
  ) => {
    return (
      <button
        ref={ref}
        type={rest.type ?? 'button'}
        data-nm="icon-button"
        data-size={size}
        data-appearance={appearance}
        aria-label={label}
        title={label}
        className={cn(
          'inline-flex items-center justify-center rounded-full text-[color:var(--nm-ink)]',
          'transition-colors duration-150',
          'hover:bg-[color:var(--nm-paper-warm)] active:opacity-80',
          'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--nm-ink)]',
          'disabled:opacity-50 disabled:cursor-not-allowed',
          appearance === 'ring' && 'border border-[color:var(--nm-hairline)]',
          ICON_BTN_SIZE[size],
          className
        )}
        {...rest}
      >
        {children}
      </button>
    );
  }
);
IconButton.displayName = 'IconButton';

// ---------------------------------------------------------------------------
// ButtonGroup — horizontal/vertical grouping with shared hairline
// ---------------------------------------------------------------------------
export interface ButtonGroupProps {
  orientation?: 'horizontal' | 'vertical';
  className?: string;
  children: ReactNode;
}

export function ButtonGroup({
  orientation = 'horizontal',
  className,
  children,
}: ButtonGroupProps) {
  return (
    <div
      data-nm="button-group"
      data-orientation={orientation}
      className={cn(
        'inline-flex',
        orientation === 'horizontal' ? 'flex-row' : 'flex-col',
        '[&>[data-nm=button]]:rounded-none [&>[data-nm=button]:first-child]:rounded-l-[var(--radius-sm)] [&>[data-nm=button]:last-child]:rounded-r-[var(--radius-sm)]',
        orientation === 'vertical' &&
          '[&>[data-nm=button]:first-child]:rounded-tl-[var(--radius-sm)] [&>[data-nm=button]:first-child]:rounded-tr-[var(--radius-sm)] [&>[data-nm=button]:first-child]:rounded-bl-none [&>[data-nm=button]:last-child]:rounded-bl-[var(--radius-sm)] [&>[data-nm=button]:last-child]:rounded-br-[var(--radius-sm)] [&>[data-nm=button]:last-child]:rounded-tr-none',
        '[&>[data-nm=button]+[data-nm=button]]:border-l-0',
        orientation === 'vertical' &&
          '[&>[data-nm=button]+[data-nm=button]]:border-t-0 [&>[data-nm=button]+[data-nm=button]]:border-l',
        className
      )}
    >
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SplitButton — primary action + dropdown arrow (caller controls dropdown)
// ---------------------------------------------------------------------------
export interface SplitButtonProps {
  variant?: NMButtonVariant;
  size?: NMButtonSize;
  children: ReactNode;
  onPrimaryClick?: () => void;
  onDropdownClick?: () => void;
  primaryLabel?: string;
  dropdownLabel?: string;
  disabled?: boolean;
  className?: string;
}

export function SplitButton({
  variant = 'primary',
  size = 'md',
  children,
  onPrimaryClick,
  onDropdownClick,
  primaryLabel,
  dropdownLabel = 'More options',
  disabled,
  className,
}: SplitButtonProps) {
  return (
    <div data-nm="split-button" className={cn('inline-flex', className)}>
      <Button
        variant={variant}
        size={size}
        onClick={onPrimaryClick}
        disabled={disabled}
        aria-label={primaryLabel}
        className="rounded-r-none"
      >
        {children}
      </Button>
      <IconButton
        label={dropdownLabel}
        size={size}
        appearance="ring"
        onClick={onDropdownClick}
        disabled={disabled}
        className={cn(
          'rounded-l-none rounded-r-[var(--radius-sm)] border-l-0',
          variant === 'primary' &&
            'bg-[color:var(--nm-ink)] text-[color:var(--nm-paper)] border-[color:var(--nm-ink)] hover:opacity-90'
        )}
      >
        <svg
          width="12"
          height="12"
          viewBox="0 0 12 12"
          aria-hidden="true"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
        >
          <path d="M3 4.5L6 7.5L9 4.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </IconButton>
    </div>
  );
}
